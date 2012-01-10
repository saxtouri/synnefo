# Copyright 2011 GRNET S.A. All rights reserved.
#
# Redistribution and use in source and binary forms, with or
# without modification, are permitted provided that the following
# conditions are met:
#
#   1. Redistributions of source code must retain the above
#      copyright notice, this list of conditions and the following
#      disclaimer.
#
#   2. Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials
#      provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY GRNET S.A. ``AS IS'' AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL GRNET S.A OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and
# documentation are those of the authors and should not be
# interpreted as representing official policies, either expressed
# or implied, of GRNET S.A.

"""
Plankton attributes are divided in 3 categories:
  - generated: They are dynamically generated and not stored anywhere.
  - user: Stored as user accessible metadata and can be modified from within
            Pithos apps. They are visible as prefixed with PLANKTON_PREFIX.
  - system: Stored as metadata that can not be modified through Pithos.

In more detail, Plankton attributes are the following:
  - checksum: the 'hash' meta
  - container_format: stored as a user meta
  - created_at: the 'modified' meta of the first version
  - deleted_at: the timestamp of the last version
  - disk_format: stored as a user meta
  - id: the 'uuid' meta
  - is_public: True if there is a * entry for the read permission
  - location: generated based on the file's path
  - name: stored as a user meta
  - owner: the file's account
  - properties: stored as user meta prefixed with PROPERTY_PREFIX
  - size: the 'bytes' meta
  - status: stored as a system meta
  - store: is always 'pithos'
  - updated_at: the 'modified' meta
"""

import json
import warnings

from binascii import hexlify
from functools import partial
from hashlib import md5
from operator import itemgetter
from time import gmtime, strftime, time
from uuid import UUID

from django.conf import settings

from pithos.backends import connect_backend
from pithos.backends.base import NotAllowedError


PITHOS_DOMAIN = 'pithos'
PLANKTON_DOMAIN = 'plankton'

PLANKTON_PREFIX = 'X-Object-Meta-plankton:'
PROPERTY_PREFIX = 'property:'

SYSTEM_META = set(['status'])
USER_META = set(['name', 'container_format', 'disk_format'])


def get_location(account, container, object):
    assert '/' not in account, "Invalid account"
    assert '/' not in container, "Invalid container"
    return 'pithos://%s/%s/%s' % (account, container, object)

def split_location(location):
    """Returns (accout, container, object) from a location string"""
    t = location.split('/', 4)
    assert len(t) == 5, "Invalid location"
    return t[2:5]


class BackendException(Exception):
    pass


class ImageBackend(object):
    """A wrapper arround the pithos backend to simplify image handling."""
    
    def __init__(self, user):
        self.user = user
        self.container = settings.PITHOS_IMAGE_CONTAINER
        
        original_filters = warnings.filters
        warnings.simplefilter('ignore')         # Suppress SQLAlchemy warnings
        self.backend = connect_backend()
        warnings.filters = original_filters     # Restore warnings
        
        try:
            self.backend.put_container(self.user, self.user, self.container)
        except NameError:
            pass    # Container already exists
    
    def _get_image(self, location):
        def format_timestamp(t):
            return strftime('%Y-%m-%d %H:%M:%S', gmtime(t))
        
        account, container, object = split_location(location)
        
        try:
            versions = self.backend.list_versions(self.user, account,
                    container, object)
        except NameError:
            return None
        
        image = {}
        
        meta = self._get_meta(location)
        if meta:
            image['deleted_at'] = ''
        else:
            # Object was deleted, use the latest version
            version, timestamp = versions[-1]
            meta = self._get_meta(location, version)
            image['deleted_at'] = format_timestamp(timestamp)
        
        if PLANKTON_PREFIX + 'name' not in meta:
            return None     # Not a Plankton image
        
        permissions = self._get_permissions(location)
        
        image['checksum'] = meta['hash']
        image['created_at'] = format_timestamp(versions[0][1])
        image['id'] = meta['uuid']
        image['is_public'] = '*' in permissions.get('read', [])
        image['location'] = location
        image['owner'] = account
        image['size'] = meta['bytes']
        image['store'] = 'pithos'
        image['updated_at'] = format_timestamp(meta['modified'])
        image['properties'] = {}
        
        for key, val in meta.items():
            if key.startswith(PLANKTON_PREFIX):
                key = key[len(PLANKTON_PREFIX):]
            
            if key in SYSTEM_META | USER_META:
                image[key] = val
            elif key.startswith(PROPERTY_PREFIX):
                key = key[len(PROPERTY_PREFIX):]
                image['properties'][key] = val
        
        return image
    
    def _get_meta(self, location, version=None, user=None):
        user = user or self.user
        account, container, object = split_location(location)
        try:
            meta = self.backend.get_object_meta(user, account, container,
                    object, PITHOS_DOMAIN, version)
            meta.update(self.backend.get_object_meta(user, account, container,
                    object, PLANKTON_DOMAIN, version))
        except NameError:
            return None
        
        return meta
    
    def _get_permissions(self, location):
        account, container, object = split_location(location)
        action, path, permissions = self.backend.get_object_permissions(
                self.user, account, container, object)
        return permissions
    
    def _store(self, f, size=None):
        """Breaks data into blocks and stores them in the backend"""
        
        bytes = 0
        hashmap = []
        backend = self.backend
        blocksize = backend.block_size
        
        data = f.read(blocksize)
        while data:
            hash = backend.put_block(data)
            hashmap.append(hash)
            bytes += len(data)
            data = f.read(blocksize)
        
        if size and size != bytes:
            raise BackendException("Invalid size")
        
        return hashmap, bytes
    
    def _update(self, location, size, hashmap, meta, permissions):
        account, container, object = split_location(location)
        self.backend.update_object_hashmap(self.user, account, container,
                object, size, hashmap, PLANKTON_DOMAIN,
                permissions=permissions)
        self._update_meta(location, meta, replace=True)
    
    def _update_meta(self, location, meta, replace=False):
        user = self.user
        account, container, object = split_location(location)

        user_meta = {}
        system_meta = {}
        for key, val in meta.items():
            if key in SYSTEM_META:
                system_meta[key] = val
            elif key in USER_META:
                user_meta[PLANKTON_PREFIX + key] = val
            elif key == 'properties':
                for k, v in val.items():
                    user_meta[PLANKTON_PREFIX + PROPERTY_PREFIX + k] = v
        
        if user_meta:
            self.backend.update_object_meta(user, account, container, object,
                    PITHOS_DOMAIN, user_meta, replace)
            replace = False
        
        if system_meta:
            self.backend.update_object_meta(user, account, container, object,
                    PLANKTON_DOMAIN, system_meta, replace)
    
    def _update_permissions(self, location, permissions):
        account, container, object = split_location(location)
        self.backend.update_object_permissions(self.user, account, container,
                object, permissions)
    
    def add_user(self, image_id, user):
        image = self.get_meta(image_id)
        assert image, "Image not found"
        
        location = image['location']
        permissions = self._get_permissions(location)
        read = set(permissions.get('read', []))
        read.add(user)
        permissions['read'] = list(read)
        self._update_permissions(location, permissions)
    
    def close(self):
        self.backend.close()
    
    def get_data(self, location):
        account, container, object = split_location(location)
        size, hashmap = self.backend.get_object_hashmap(self.user, account,
                container, object)
        data = ''.join(self.backend.get_block(hash) for hash in hashmap)
        assert len(data) == size
        return data
    
    def get_meta(self, image_id):
        try:
            account, container, object = self.backend.get_uuid(self.user,
                    image_id)
        except NameError:
            return None
        
        location = get_location(account, container, object)
        return self._get_image(location)
    
    def iter_public(self, filters):
        backend = self.backend
        
        keys = set()
        for key, val in filters.items():
            if key == 'size_min':
                filter = 'bytes >= %d' % size_min
            elif key == 'size_max':
                filter = 'bytes <= %d' % size_max
            else:
                # XXX Only filters for user meta supported
                filter = '%s = %s' % (PLANKTON_PREFIX + key, val)
            keys.add(filter)
        
        container = self.container
        
        for account in backend.list_accounts(None):
            for container in backend.list_containers(None, account,
                                                     shared=True):
                for path, version_id in backend.list_objects(None, account,
                        container, prefix='', delimiter='/',
                        domain=PITHOS_DOMAIN,
                        keys=keys, shared=True):
                    location = get_location(account, container, path)
                    image = self._get_image(location)
                    if image:
                        yield image
    
    def iter_shared(self, member):
        """Iterate over image ids shared to this member"""
        
        backend = self.backend
        
        # To get the list we connect as member and get the list shared by us
        for container in  backend.list_containers(member, self.user):
            for path, version_id in backend.list_objects(member, self.user,
                    container, prefix='', delimiter='/', domain=PITHOS_DOMAIN):
                try:
                    location = get_location(self.user, container, path)
                    meta = self._get_meta(location, user=member)
                    if PLANKTON_PREFIX + 'name' in meta:
                        yield meta['uuid']
                except NotAllowedError:
                    continue
    
    def list_public(self, filters, params):
        images = list(self.iter_public(filters))
        key = itemgetter(params.get('sort_key', 'created_at'))
        reverse = params.get('sort_dir', 'desc') == 'desc'
        images.sort(key=key, reverse=reverse)
        return images
    
    def list_users(self, image_id):
        image = self.get_meta(image_id)
        assert image, "Image not found"
        
        permissions = self._get_permissions(image['location'])
        return [user for user in permissions.get('read', []) if user != '*']
    
    def put(self, name, f, params):
        assert 'checksum' not in params, "Passing a checksum is not supported"
        assert 'id' not in params, "Passing an ID is not supported"
        assert params.pop('store', 'pithos') == 'pithos', "Invalid store"
        assert params.setdefault('disk_format',
                settings.DEFAULT_DISK_FORMAT) in \
                settings.ALLOWED_DISK_FORMATS, "Invalid disk_format"
        assert params.setdefault('container_format',
                settings.DEFAULT_CONTAINER_FORMAT) in \
                settings.ALLOWED_CONTAINER_FORMATS, "Invalid container_format"
        
        filename = params.pop('filename', name)
        location = 'pithos://%s/%s/%s' % (self.user, self.container, filename)
        is_public = params.pop('is_public', False)
        permissions = {'read': ['*']} if is_public else {}
        size = params.pop('size', None)
        
        hashmap, size = self._store(f, size)
        
        meta = {}
        meta['properties'] = params.pop('properties', {})
        meta.update(name=name, status='available', **params)
        
        self._update(location, size, hashmap, meta, permissions)
        return self._get_image(location)
    
    def register(self, name, location, params):
        assert 'id' not in params, "Passing an ID is not supported"
        assert location.startswith('pithos://'), "Invalid location"
        assert params.pop('store', 'pithos') == 'pithos', "Invalid store"
        assert params.setdefault('disk_format',
                settings.DEFAULT_DISK_FORMAT) in \
                settings.ALLOWED_DISK_FORMATS, "Invalid disk_format"
        assert params.setdefault('container_format',
                settings.DEFAULT_CONTAINER_FORMAT) in \
                settings.ALLOWED_CONTAINER_FORMATS, "Invalid container_format"
        
        user = self.user
        account, container, object = split_location(location)
        
        meta = self._get_meta(location)
        assert meta, "File not found"
        
        size = params.pop('size', meta['bytes'])
        if size != meta['bytes']:
            raise BackendException("Invalid size")
        
        checksum = params.pop('checksum', meta['hash'])
        if checksum != meta['hash']:
            raise BackendException("Invalid checksum")
        
        is_public = params.pop('is_public', False)
        permissions = {'read': ['*']} if is_public else {}
        
        meta = {}
        meta['properties'] = params.pop('properties', {})
        meta.update(name=name, status='available', **params)
        
        self._update_meta(location, meta)
        self._update_permissions(location, permissions)
        return self._get_image(location)
    
    def remove_user(self, image_id, user):
        image = self.get_meta(image_id)
        assert image, "Image not found"
        
        location = image['location']
        permissions = self._get_permissions(location)
        try:
            permissions.get('read', []).remove(user)
        except ValueError:
            return      # User did not have access anyway
        self._update_permissions(location, permissions)
    
    def replace_users(self, image_id, users):
        image = self.get_meta(image_id)
        assert image, "Image not found"
        
        location = image['location']
        permissions = self._get_permissions(location)
        permissions['read'] = users
        if image.get('is_public', False):
            permissions['read'].append('*')
        self._update_permissions(location, permissions)
    
    def update(self, image_id, params):
        image = self.get_meta(image_id)
        assert image, "Image not found"
        
        location = image['location']
        is_public = params.pop('is_public', None)
        if is_public is not None:
            permissions = self._get_permissions(location)
            read = set(permissions.get('read', []))
            if is_public:
                read.add('*')
            else:
                read.discard('*')
            permissions['read'] = list(read)
            self.backend._update_permissions(location, permissions)
        
        meta = {}
        meta['properties'] = params.pop('properties', {})
        meta.update(**params)
        
        self._update_meta(location, meta)
        return self.get_meta(image_id)
