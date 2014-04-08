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

import json

from logging import getLogger
from string import punctuation
from urllib import unquote

from django.conf import settings
from django.http import HttpResponse

from snf_django.lib import api
from snf_django.lib.api import faults
from synnefo.util.text import uenc
from synnefo.plankton.backend import PlanktonBackend
from synnefo.plankton.backend import split_url


FILTERS = ('name', 'container_format', 'disk_format', 'status', 'size_min',
           'size_max')

PARAMS = ('sort_key', 'sort_dir')

SORT_KEY_OPTIONS = ('id', 'name', 'status', 'size', 'disk_format',
                    'container_format', 'created_at', 'updated_at')

SORT_DIR_OPTIONS = ('asc', 'desc')

LIST_FIELDS = ('status', 'name', 'disk_format', 'container_format', 'size',
               'id')

DETAIL_FIELDS = ('name', 'disk_format', 'container_format', 'size', 'checksum',
                 'location', 'created_at', 'updated_at', 'deleted_at',
                 'status', 'is_public', 'owner', 'properties', 'id',
                 "is_snapshot")

ADD_FIELDS = ('name', 'id', 'store', 'disk_format', 'container_format', 'size',
              'checksum', 'is_public', 'owner', 'properties', 'location')

UPDATE_FIELDS = ('name', 'disk_format', 'container_format', 'is_public',
                 'owner', 'properties', 'status')

DISK_FORMATS = ('diskdump', 'extdump', 'ntfsdump')

CONTAINER_FORMATS = ('aki', 'ari', 'ami', 'bare', 'ovf')

STORE_TYPES = ('pithos')


log = getLogger('synnefo.plankton')


API_STATUS_FROM_IMAGE_STATUS = {
    "CREATING": "SAVING",
    "AVAILABLE": "AVAILABLE",
    "DELETED": "DELETED"}


def _create_image_response(image):
    response = HttpResponse()

    for key in DETAIL_FIELDS:
        if key == 'properties':
            for k, v in image.get('properties', {}).items():
                name = 'x-image-meta-property-' + k.replace('_', '-')
                response[name] = uenc(v)
        else:
            if key == "status":
                img_status = image.get(key, "").upper()
                status = API_STATUS_FROM_IMAGE_STATUS.get(img_status,
                                                          "UNKNOWN")
                response["x-image-meta-status"] = status
            else:
                name = 'x-image-meta-' + key.replace('_', '-')
                response[name] = uenc(image.get(key, ''))

    return response


def _get_image_headers(request):
    def normalize(s):
        return ''.join('_' if c in punctuation else c.lower() for c in s)

    META_PREFIX = 'HTTP_X_IMAGE_META_'
    META_PREFIX_LEN = len(META_PREFIX)
    META_PROPERTY_PREFIX = 'HTTP_X_IMAGE_META_PROPERTY_'
    META_PROPERTY_PREFIX_LEN = len(META_PROPERTY_PREFIX)

    headers = {'properties': {}}

    for key, val in request.META.items():
        if key.startswith(META_PROPERTY_PREFIX):
            name = normalize(key[META_PROPERTY_PREFIX_LEN:])
            headers['properties'][unquote(name)] = unquote(uenc(val))
        elif key.startswith(META_PREFIX):
            name = normalize(key[META_PREFIX_LEN:])
            headers[unquote(name)] = unquote(uenc(val))

    is_public = headers.get('is_public', None)
    if is_public is not None:
        headers['is_public'] = True if is_public.lower() == 'true' else False

    if not headers['properties']:
        del headers['properties']

    return headers


@api.api_method(http_method="POST", user_required=True, logger=log)
def add_image(request):
    """Add a new virtual machine image

    Described in:
    3.6. Adding a New Virtual Machine Image

    Implementation notes:
      * The implementation is very inefficient as it loads the whole image
        in memory.

    Limitations:
      * x-image-meta-id is not supported. Will always return 409 Conflict.

    Extensions:
      * An x-image-meta-location header can be passed with a link to file,
        instead of uploading the data.
    """

    params = _get_image_headers(request)
    log.debug('add_image %s', params)

    if not set(params.keys()).issubset(set(ADD_FIELDS)):
        raise faults.BadRequest("Invalid parameters")

    name = params.pop('name', None)
    if name is None:
        raise faults.BadRequest("Image 'name' parameter is required")
    elif len(uenc(name)) == 0:
        raise faults.BadRequest("Invalid image name")
    location = params.pop('location', None)
    if location is None:
        raise faults.BadRequest("'location' parameter is required")

    try:
        split_url(location)
    except AssertionError:
        raise faults.BadRequest("Invalid location '%s'" % location)

    validate_fields(params)

    if location:
        with PlanktonBackend(request.user_uniq) as backend:
            image = backend.register(name, location, params)
    else:
        #f = StringIO(request.body)
        #image = backend.put(name, f, params)
        return HttpResponse(status=501)     # Not Implemented

    if not image:
        return HttpResponse('Registration failed', status=500)

    return _create_image_response(image)


@api.api_method(http_method="DELETE", user_required=True, logger=log)
def delete_image(request, image_id):
    """Delete an Image.

    This API call is not described in the Openstack Glance API.

    Implementation notes:
      * The implementation does not delete the Image from the storage
        backend. Instead it unregisters the image by removing all the
        metadata from the plankton metadata domain.

    """
    log.info("delete_image '%s'" % image_id)
    userid = request.user_uniq
    with PlanktonBackend(userid) as backend:
        backend.unregister(image_id)
    log.info("User '%s' deleted image '%s'" % (userid, image_id))
    return HttpResponse(status=204)


@api.api_method(http_method="PUT", user_required=True, logger=log)
def add_image_member(request, image_id, member):
    """Add a member to an image

    Described in:
    3.9. Adding a Member to an Image

    Limitations:
      * Passing a body to enable `can_share` is not supported.
    """

    log.debug('add_image_member %s %s', image_id, member)
    with PlanktonBackend(request.user_uniq) as backend:
        backend.add_user(image_id, member)
    return HttpResponse(status=204)


@api.api_method(http_method="GET", user_required=True, logger=log)
def get_image(request, image_id):
    """Retrieve a virtual machine image

    Described in:
    3.5. Retrieving a Virtual Machine Image

    Implementation notes:
      * The implementation is very inefficient as it loads the whole image
        in memory.
    """

    #image = backend.get_image(image_id)
    #if not image:
    #    return HttpResponseNotFound()
    #
    #response = _create_image_response(image)
    #data = backend.get_data(image)
    #response.content = data
    #response['Content-Length'] = len(data)
    #response['Content-Type'] = 'application/octet-stream'
    #response['ETag'] = image['checksum']
    #return response
    return HttpResponse(status=501)     # Not Implemented


@api.api_method(http_method="HEAD", user_required=True, logger=log)
def get_image_meta(request, image_id):
    """Return detailed metadata on a specific image

    Described in:
    3.4. Requesting Detailed Metadata on a Specific Image
    """

    with PlanktonBackend(request.user_uniq) as backend:
        image = backend.get_image(image_id)
    return _create_image_response(image)


@api.api_method(http_method="GET", user_required=True, logger=log)
def list_image_members(request, image_id):
    """List image memberships

    Described in:
    3.7. Requesting Image Memberships
    """

    with PlanktonBackend(request.user_uniq) as backend:
        users = backend.list_users(image_id)

    members = [{'member_id': u, 'can_share': False} for u in users]
    data = json.dumps({'members': members}, indent=settings.DEBUG)
    return HttpResponse(data)


@api.api_method(http_method="GET", user_required=True, logger=log)
def list_images(request, detail=False):
    """Return a list of available images.

    This includes images owned by the user, images shared with the user and
    public images.

    """

    def get_request_params(keys):
        params = {}
        for key in keys:
            val = request.GET.get(key, None)
            if val is not None:
                params[key] = val
        return params

    log.debug('list_public_images detail=%s', detail)

    filters = get_request_params(FILTERS)
    params = get_request_params(PARAMS)

    params.setdefault('sort_key', 'created_at')
    params.setdefault('sort_dir', 'desc')

    if not params['sort_key'] in SORT_KEY_OPTIONS:
        raise faults.BadRequest("Invalid 'sort_key'")
    if not params['sort_dir'] in SORT_DIR_OPTIONS:
        raise faults.BadRequest("Invalid 'sort_dir'")

    if 'size_max' in filters:
        try:
            filters['size_max'] = int(filters['size_max'])
        except ValueError:
            raise faults.BadRequest("Malformed request.")

    if 'size_min' in filters:
        try:
            filters['size_min'] = int(filters['size_min'])
        except ValueError:
            raise faults.BadRequest("Malformed request.")

    with PlanktonBackend(request.user_uniq) as backend:
        images = backend.list_images(filters, params)

    # Remove keys that should not be returned
    fields = DETAIL_FIELDS if detail else LIST_FIELDS
    for image in images:
        for key in image.keys():
            if key not in fields:
                del image[key]

    data = json.dumps(images, indent=settings.DEBUG)
    return HttpResponse(data)


@api.api_method(http_method="GET", user_required=True, logger=log)
def list_shared_images(request, member):
    """Request shared images

    Described in:
    3.8. Requesting Shared Images

    Implementation notes:
      * It is not clear what this method should do. We return the IDs of
        the users's images that are accessible by `member`.
    """

    log.debug('list_shared_images %s', member)

    images = []
    with PlanktonBackend(request.user_uniq) as backend:
        for image in backend.list_shared_images(member=member):
            images.append({'image_id': image["id"], 'can_share': False})

    data = json.dumps({'shared_images': images}, indent=settings.DEBUG)
    return HttpResponse(data)


@api.api_method(http_method="DELETE", user_required=True, logger=log)
def remove_image_member(request, image_id, member):
    """Remove a member from an image

    Described in:
    3.10. Removing a Member from an Image
    """

    log.debug('remove_image_member %s %s', image_id, member)
    with PlanktonBackend(request.user_uniq) as backend:
        backend.remove_user(image_id, member)
    return HttpResponse(status=204)


@api.api_method(http_method="PUT", user_required=True, logger=log)
def update_image(request, image_id):
    """Update an image

    Described in:
    3.6.2. Updating an Image

    Implementation notes:
      * It is not clear which metadata are allowed to be updated. We support:
        name, disk_format, container_format, is_public, owner, properties
        and status.
    """

    meta = _get_image_headers(request)
    log.debug('update_image %s', meta)

    if not set(meta.keys()).issubset(set(UPDATE_FIELDS)):
        raise faults.BadRequest("Invalid metadata")

    validate_fields(meta)

    with PlanktonBackend(request.user_uniq) as backend:
        image = backend.update_metadata(image_id, meta)
    return _create_image_response(image)


@api.api_method(http_method="PUT", user_required=True, logger=log)
def update_image_members(request, image_id):
    """Replace a membership list for an image

    Described in:
    3.11. Replacing a Membership List for an Image

    Limitations:
      * can_share value is ignored
    """

    log.debug('update_image_members %s', image_id)
    data = api.utils.get_json_body(request)
    members = []

    memberships = api.utils.get_attribute(data, "memberships", attr_type=list)
    for member in memberships:
        if not isinstance(member, dict):
            raise faults.BadRequest("Invalid 'memberships' field")
        member = api.utils.get_attribute(member, "member_id")
        members.append(member)

    with PlanktonBackend(request.user_uniq) as backend:
        backend.replace_users(image_id, members)
    return HttpResponse(status=204)


def validate_fields(params):
    if "id" in params:
        raise faults.BadRequest("Setting the image ID is not supported")

    if "store" in params:
        if params["store"] not in STORE_TYPES:
            raise faults.BadRequest("Invalid store type '%s'" %
                                    params["store"])

    if "disk_format" in params:
        if params["disk_format"] not in DISK_FORMATS:
            raise faults.BadRequest("Invalid disk format '%s'" %
                                    params['disk_format'])

    if "container_format" in params:
        if params["container_format"] not in CONTAINER_FORMATS:
            raise faults.BadRequest("Invalid container format '%s'" %
                                    params['container_format'])
