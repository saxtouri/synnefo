# Copyright 2011-2014 GRNET S.A. All rights reserved.

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
The Plankton attributes are the following:
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
import logging
import os

from time import time, gmtime, strftime
from functools import wraps
from operator import itemgetter
from collections import namedtuple
from copy import deepcopy

from urllib import quote, unquote
from django.conf import settings
from django.utils import importlib
from django.utils.encoding import smart_unicode, smart_str
from pithos.backends.base import NotAllowedError, VersionNotExists, QuotaError
from snf_django.lib.api import faults

Location = namedtuple("ObjectLocation", ["account", "container", "path"])

logger = logging.getLogger(__name__)


PLANKTON_DOMAIN = 'plankton'
PLANKTON_PREFIX = 'plankton:'
PROPERTY_PREFIX = 'property:'

PLANKTON_META = ('container_format', 'disk_format', 'name',
                 'status', 'created_at', 'volume_id', 'description')

MAX_META_KEY_LENGTH = 128 - len(PLANKTON_DOMAIN) - len(PROPERTY_PREFIX)
MAX_META_VALUE_LENGTH = 256


from pithos.backends.util import PithosBackendPool
_pithos_backend_pool = \
    PithosBackendPool(
        settings.PITHOS_BACKEND_POOL_SIZE,
        astakos_auth_url=settings.ASTAKOS_AUTH_URL,
        service_token=settings.CYCLADES_SERVICE_TOKEN,
        astakosclient_poolsize=settings.CYCLADES_ASTAKOSCLIENT_POOLSIZE,
        db_connection=settings.BACKEND_DB_CONNECTION,
        block_path=settings.BACKEND_BLOCK_PATH)


def get_pithos_backend():
    return _pithos_backend_pool.pool_get()


def format_timestamp(t):
    return strftime('%Y-%m-%d %H:%M:%S', gmtime(t))


def handle_pithos_backend(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        backend = self.backend
        backend.pre_exec()
        commit = False
        try:
            ret = func(self, *args, **kwargs)
        except NotAllowedError:
            raise faults.Forbidden
        except (NameError, VersionNotExists):
            raise faults.ItemNotFound
        except (AssertionError, ValueError):
            raise faults.BadRequest
        except QuotaError:
            raise faults.OverLimit
        else:
            commit = True
        finally:
            backend.post_exec(commit)
        return ret
    return wrapper


class PlanktonBackend(object):
    """A wrapper arround the pithos backend to simplify image handling."""

    def __init__(self, user):
        self.user = user
        self.backend = get_pithos_backend()

    def close(self):
        """Close PithosBackend(return to pool)"""
        self.backend.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        self.backend = None
        return False

    @handle_pithos_backend
    def get_image(self, uuid):
        return self._get_image(uuid)

    def _get_image(self, uuid):
        location, metadata = self._get_raw_metadata(uuid)
        permissions = self._get_raw_permissions(uuid, location)
        return image_to_dict(location, metadata, permissions)

    @handle_pithos_backend
    def add_property(self, uuid, key, value):
        location, _ = self._get_raw_metadata(uuid)
        properties = self._prefix_properties({key: value})
        self._update_metadata(uuid, location, properties, replace=False)

    @handle_pithos_backend
    def remove_property(self, uuid, key):
        location, _ = self._get_raw_metadata(uuid)
        # Use empty string to delete a property
        properties = self._prefix_properties({key: ""})
        self._update_metadata(uuid, location, properties, replace=False)

    @handle_pithos_backend
    def update_properties(self, uuid, properties):
        location, _ = self._get_raw_metadata(uuid)
        properties = self._prefix_properties(properties)
        self._update_metadata(uuid, location, properties, replace=False)

    @staticmethod
    def _prefix_properties(properties):
        """Add property prefix to properties."""
        return dict([(PROPERTY_PREFIX + k, v) for k, v in properties.items()])

    @staticmethod
    def _unprefix_properties(properties):
        """Remove property prefix from properties."""
        return dict([(k.replace(PROPERTY_PREFIX, "", 1), v)
                     for k, v in properties.items()])

    @staticmethod
    def _prefix_metadata(metadata):
        """Add plankton prefix to metadata."""
        return dict([(PLANKTON_PREFIX + k, v) for k, v in metadata.items()])

    @staticmethod
    def _unprefix_metadata(metadata):
        """Remove plankton prefix from metadata."""
        return dict([(k.replace(PLANKTON_PREFIX, "", 1), v)
                     for k, v in metadata.items()])

    @handle_pithos_backend
    def update_metadata(self, uuid, metadata):
        location, _ = self._get_raw_metadata(uuid)

        is_public = metadata.pop("is_public", None)
        if is_public is not None:
            self._set_public(uuid, location, public=is_public)

        # Each property is stored as a separate prefixed metadata
        meta = deepcopy(metadata)
        properties = meta.pop("properties", {})
        meta.update(self._prefix_properties(properties))

        self._update_metadata(uuid, location, metadata=meta, replace=False)

        return self._get_image(uuid)

    def _update_metadata(self, uuid, location, metadata, replace=False):
        _prefixed_metadata = self._prefix_metadata(metadata)
        prefixed = {}
        for k, v in _prefixed_metadata.items():
            # Encode to UTF-8
            k, v = smart_unicode(k), smart_unicode(v)
            # Check the length of key/value
            if len(k) > 128:
                raise faults.BadRequest('Metadata keys should be less than %s'
                                        ' characters' % MAX_META_KEY_LENGTH)
            if len(v) > 256:
                raise faults.BadRequest('Metadata values should be less than'
                                        ' %scharacters.'
                                        % MAX_META_VALUE_LENGTH)
            prefixed[k] = v

        account, container, path = location
        self.backend.update_object_meta(self.user, account, container, path,
                                        PLANKTON_DOMAIN, prefixed, replace)
        logger.debug("User '%s' updated image '%s', metadata: '%s'", self.user,
                     uuid, prefixed)

    def _get_raw_metadata(self, uuid, version=None, check_image=True):
        """Get info and metadata in Plankton doamin for the Pithos object.

        Return the location and the metadata of the Pithos object.
        If 'check_image' is set, check that the Pithos object is a registered
        Plankton Image.

        """
        # Convert uuid to location
        account, container, path = self.backend.get_uuid(self.user, uuid)
        try:
            meta = self.backend.get_object_meta(self.user, account, container,
                                                path, PLANKTON_DOMAIN, version)
            meta["deleted"] = False
        except NameError:
            if version is not None:
                raise
            versions = self.backend.list_versions(self.user, account,
                                                  container, path)
            assert(versions), ("Object without versions: %s/%s/%s" %
                               (account, container, path))
            # Object was deleted, use the latest version
            version, timestamp = versions[-1]
            meta = self.backend.get_object_meta(self.user, account, container,
                                                path, PLANKTON_DOMAIN, version)
            meta["deleted"] = True

        if check_image and PLANKTON_PREFIX + "name" not in meta:
            # Check that object is an image by checking if it has an Image name
            # in Plankton metadata
            raise faults.ItemNotFound("Image '%s' does not exist." % uuid)

        return Location(account, container, path), meta

    # Users and Permissions
    @handle_pithos_backend
    def add_user(self, uuid, user):
        assert(isinstance(user, basestring))
        location, _ = self._get_raw_metadata(uuid)
        permissions = self._get_raw_permissions(uuid, location)
        read = set(permissions.get("read", []))
        if not user in read:
            read.add(user)
            permissions["read"] = list(read)
            self._update_permissions(uuid, location, permissions)

    @handle_pithos_backend
    def remove_user(self, uuid, user):
        assert(isinstance(user, basestring))
        location, _ = self._get_raw_metadata(uuid)
        permissions = self._get_raw_permissions(uuid, location)
        read = set(permissions.get("read", []))
        if user in read:
            read.remove(user)
            permissions["read"] = list(read)
            self._update_permissions(uuid, location, permissions)

    @handle_pithos_backend
    def replace_users(self, uuid, users):
        assert(isinstance(users, list))
        location, _ = self._get_raw_metadata(uuid)
        permissions = self._get_raw_permissions(uuid, location)
        read = set(permissions.get("read", []))
        if "*" in read:  # Retain public permissions
            users.append("*")
        permissions["read"] = list(users)
        self._update_permissions(uuid, location, permissions)

    @handle_pithos_backend
    def list_users(self, uuid):
        location, _ = self._get_raw_metadata(uuid)
        permissions = self._get_raw_permissions(uuid, location)
        return [user for user in permissions.get('read', []) if user != '*']

    def _set_public(self, uuid, location, public):
        permissions = self._get_raw_permissions(uuid, location)
        assert(isinstance(public, bool))
        read = set(permissions.get("read", []))
        if public and "*" not in read:
            read.add("*")
        elif not public and "*" in read:
            read.discard("*")
        permissions["read"] = list(read)
        self._update_permissions(uuid, location, permissions)
        return permissions

    def _get_raw_permissions(self, uuid, location):
        account, container, path = location
        _a, path, permissions = \
            self.backend.get_object_permissions(self.user, account, container,
                                                path)

        if path is None and permissions != {}:
            raise Exception("Database Inconsistency Error:"
                            " Image '%s' got permissions from 'None' path." %
                            uuid)

        return permissions

    def _update_permissions(self, uuid, location, permissions):
        account, container, path = location
        self.backend.update_object_permissions(self.user, account, container,
                                               path, permissions)
        logger.debug("User '%s' updated image '%s' permissions: '%s'",
                     self.user, uuid, permissions)

    @handle_pithos_backend
    def register(self, name, image_url, metadata):
        # Validate that metadata are allowed
        if "id" in metadata:
            raise faults.BadRequest("Passing an ID is not supported")
        store = metadata.pop("store", "pithos")
        if store != "pithos":
            raise faults.BadRequest("Invalid store '%s'. Only 'pithos' store"
                                    " is supported" % store)
        disk_format = metadata.setdefault("disk_format",
                                          settings.DEFAULT_DISK_FORMAT)
        if disk_format not in settings.ALLOWED_DISK_FORMATS:
            raise faults.BadRequest("Invalid disk format '%s'" % disk_format)
        container_format =\
            metadata.setdefault("container_format",
                                settings.DEFAULT_CONTAINER_FORMAT)
        if container_format not in settings.ALLOWED_CONTAINER_FORMATS:
            raise faults.BadRequest("Invalid container format '%s'" %
                                    container_format)

        account, container, path = split_url(image_url)
        location = Location(account, container, path)
        meta = self.backend.get_object_meta(self.user, account, container,
                                            path, PLANKTON_DOMAIN, None)
        uuid = meta["uuid"]

        # Validate that 'size' and 'checksum'
        size = metadata.pop('size', int(meta['bytes']))
        if not isinstance(size, int) or int(size) != int(meta["bytes"]):
            raise faults.BadRequest("Invalid 'size' field")

        checksum = metadata.pop('checksum', meta['hash'])
        if not isinstance(checksum, basestring) or checksum != meta['hash']:
            raise faults.BadRequest("Invalid checksum field")

        users = [self.user]
        public = metadata.pop("is_public", False)
        if not isinstance(public, bool):
            raise faults.BadRequest("Invalid value for 'is_public' metadata")
        if public:
            users.append("*")
        permissions = {'read': users}
        self._update_permissions(uuid, location, permissions)

        # Each property is stored as a separate prefixed metadata
        meta = deepcopy(metadata)
        properties = meta.pop("properties", {})
        meta.update(self._prefix_properties(properties))
        # Add extra metadata
        meta["name"] = name
        meta["status"] = "AVAILABLE"
        meta['created_at'] = str(time())
        #meta["is_snapshot"] = False
        self._update_metadata(uuid, location, metadata=meta, replace=False)

        logger.debug("User '%s' registered image '%s'('%s')", self.user,
                     uuid, location)
        return self._get_image(uuid)

    @handle_pithos_backend
    def unregister(self, uuid):
        """Unregister an Image.

        Unregister an Image by removing all the metadata in the Plankton
        domain. The Pithos file is not deleted.

        """
        location, _ = self._get_raw_metadata(uuid)
        self._update_metadata(uuid, location, metadata={}, replace=True)
        logger.debug("User '%s' unregistered image '%s'", self.user, uuid)

    # List functions
    def _list_images(self, user=None, filters=None, params=None):
        filters = filters or {}

        # TODO: Use filters
        # # Fix keys
        # keys = [PLANKTON_PREFIX + 'name']
        # size_range = (None, None)
        # for key, val in filters.items():
        #     if key == 'size_min':
        #         size_range = (val, size_range[1])
        #     elif key == 'size_max':
        #         size_range = (size_range[0], val)
        #     else:
        #         keys.append('%s = %s' % (PLANKTON_PREFIX + key, val))
        _images = self.backend.get_domain_objects(domain=PLANKTON_DOMAIN,
                                                  user=user)

        images = []
        for (location, metadata, permissions) in _images:
            location = Location(*location.split("/", 2))
            images.append(image_to_dict(location, metadata, permissions))

        if params is None:
            params = {}

        key = itemgetter(params.get('sort_key', 'created_at'))
        reverse = params.get('sort_dir', 'desc') == 'desc'
        images.sort(key=key, reverse=reverse)
        return images

    @handle_pithos_backend
    def list_images(self, filters=None, params=None):
        return self._list_images(user=self.user, filters=filters,
                                 params=params)

    @handle_pithos_backend
    def list_shared_images(self, member, filters=None, params=None):
        images = self._list_images(user=self.user, filters=filters,
                                   params=params)
        is_shared = lambda img: not img["is_public"] and img["owner"] == member
        return filter(is_shared, images)

    @handle_pithos_backend
    def list_public_images(self, filters=None, params=None):
        images = self._list_images(user=None, filters=filters, params=params)
        return filter(lambda img: img["is_public"], images)

    # Snapshots
    def list_snapshots(self, user=None):
        _snapshots = self.list_images()
        return [s for s in _snapshots if s["is_snapshot"]]

    @handle_pithos_backend
    def get_snapshot(self, user, snapshot_uuid):
        snap = self._get_image(snapshot_uuid)
        if snap.get("is_snapshot", False) is False:
            raise faults.ItemNotFound("Snapshots '%s' does not exist" %
                                      snapshot_uuid)
        return snap

    @handle_pithos_backend
    def delete_snapshot(self, snapshot_uuid):
        self.backend.delete_object_for_uuid(self.user, snapshot_uuid)

    @handle_pithos_backend
    def update_status(self, image_uuid, status):
        """Update status of snapshot"""
        location, _ = self._get_raw_metadata(image_uuid)
        properties = {"status": status.upper()}
        self._update_metadata(image_uuid, location, properties, replace=False)
        return self._get_image(image_uuid)


def create_url(account, container, name):
    """Create a Pithos URL from the object info"""
    assert "/" not in account, "Invalid account"
    assert "/" not in container, "Invalid container"
    account = quote(smart_str(account, encoding="utf-8"))
    container = quote(smart_str(container, encoding="utf-8"))
    name = quote(smart_str(name, encoding="utf-8"))
    return "pithos://%s/%s/%s" % (account, container, name)


def split_url(url):
    """Get object info from the Pithos URL"""
    assert(isinstance(url, basestring))
    t = url.split('/', 4)
    assert t[0] == "pithos:", "Invalid url"
    assert len(t) == 5, "Invalid url"
    account, container, name = t[2:5]
    parse = lambda x: smart_unicode(unquote(x), encoding="utf-8")
    return parse(account), parse(container), parse(name)


def image_to_dict(location, metadata, permissions):
    """Render an image to a dictionary"""
    account, container, name = location

    image = {}
    image["id"] = metadata["uuid"]
    image["mapfile"] = metadata["hash"]
    image["checksum"] = metadata["hash"]
    image["location"] = create_url(account, container, name)
    image["size"] = metadata["bytes"]
    image['owner'] = account
    image["store"] = u"pithos"
    image["is_snapshot"] = metadata.pop(PLANKTON_PREFIX + "is_snapshot", False)
    # Permissions
    users = list(permissions.get("read", []))
    image["is_public"] = "*" in users
    image["users"] = [u for u in users if u != "*"]
    # Timestamps
    updated_at = metadata["version_timestamp"]
    created_at = metadata.get("created_at", updated_at)
    image["created_at"] = format_timestamp(created_at)
    image["updated_at"] = format_timestamp(updated_at)
    if metadata.get("deleted", False):
        image["deleted_at"] = image["updated_at"]
    else:
        image["deleted_at"] = ""

    properties = {}
    for key, val in metadata.items():
        # Get plankton properties
        if key.startswith(PLANKTON_PREFIX):
            # Remove plankton prefix
            key = key.replace(PLANKTON_PREFIX, "")
            # Keep only those in plankton metadata
            if key in PLANKTON_META:
                if key == "status":
                    image["status"] = val.upper()
                if key != "created_at":
                    # created timestamp is return in 'created_at' field
                    image[key] = val
            elif key.startswith(PROPERTY_PREFIX):
                key = key.replace(PROPERTY_PREFIX, "")
                properties[key] = val
    image["properties"] = properties

    return image


class JSONFileBackend(object):
    """
    A dummy image backend that loads available images from a file with json
    formatted content.

    usage:
        PLANKTON_BACKEND_MODULE = 'synnefo.plankton.backend.JSONFileBackend'
        PLANKTON_IMAGES_JSON_BACKEND_FILE = '/tmp/images.json'

        # loading images from an existing plankton service
        $ curl -H "X-Auth-Token: <MYTOKEN>" \
                https://cyclades.synnefo.org/plankton/images/detail | \
                python -m json.tool > /tmp/images.json
    """
    def __init__(self, userid):
        self.images_file = getattr(settings,
                                   'PLANKTON_IMAGES_JSON_BACKEND_FILE', '')
        if not os.path.exists(self.images_file):
            raise Exception("Invalid plankgon images json backend file: %s",
                            self.images_file)
        fp = file(self.images_file)
        self.images = json.load(fp)
        fp.close()

    def iter(self, *args, **kwargs):
        return self.images.__iter__()

    def list_images(self, *args, **kwargs):
        return self.images

    def get_image(self, image_uuid):
        try:
            return filter(lambda i: i['id'] == image_uuid, self.images)[0]
        except IndexError:
            raise Exception("Unknown image uuid: %s" % image_uuid)

    def close(self):
        pass


def get_backend():
    backend_module = getattr(settings, 'PLANKTON_BACKEND_MODULE', None)
    if not backend_module:
        # no setting set
        return PlanktonBackend

    parts = backend_module.split(".")
    module = ".".join(parts[:-1])
    cls = parts[-1]
    try:
        return getattr(importlib.import_module(module), cls)
    except (ImportError, AttributeError), e:
        raise ImportError("Cannot import plankton module: %s (%s)" %
                          (backend_module, e.message))
