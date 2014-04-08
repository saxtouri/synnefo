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

import datetime
from dateutil.parser import parse as date_parse
from django.utils import simplejson as json

from django.conf import settings
from snf_django.lib.api import faults


class UTC(datetime.tzinfo):
    """
    Helper UTC time information object.
    """

    def utcoffset(self, dt):
        return datetime.timedelta(0)

    def tzname(self, dt):
        return 'UTC'

    def dst(self, dt):
        return datetime.timedelta(0)


def isoformat(d):
    """Return an ISO8601 date string that includes a timezone.

    >>> from datetime import datetime
    >>> d = datetime(2012, 8, 10, 00, 59, 59)
    >>> isoformat(d)
    '2012-08-10T00:59:59+00:00'
    """

    return d.replace(tzinfo=UTC()).isoformat()


def isoparse(s):
    """Parse an ISO8601 date string into a datetime object."""

    if not s:
        return None

    try:
        since = date_parse(s)
        utc_since = since.astimezone(UTC()).replace(tzinfo=None)
    except ValueError:
        raise faults.BadRequest('Invalid changes-since parameter.')

    now = datetime.datetime.now()
    if utc_since > now:
        raise faults.BadRequest('changes-since value set in the future.')

    if now - utc_since > datetime.timedelta(seconds=settings.POLL_LIMIT):
        raise faults.BadRequest('Too old changes-since value.')

    return utc_since


def get_json_body(request):
    """Get the JSON request body as a Python object.

    Check that the content type is json and deserialize the body of the
    request that contains a JSON document to a Python object.

    """
    data = request.body
    content_type = request.META.get("CONTENT_TYPE")
    if content_type is None:
        raise faults.BadRequest("Missing Content-Type header field")
    if content_type.startswith("application/json"):
        try:
            return json.loads(data)
        except UnicodeDecodeError:
            raise faults.BadRequest("Could not decode request as UTF-8 string")
        except ValueError:
            raise faults.BadRequest("Could not decode request body as JSON")
    else:
        raise faults.BadRequest("Unsupported Content-type: '%s'" %
                                content_type)


def prefix_pattern(prefix, append_slash=True):
    """Return a reliable urls.py pattern from a prefix"""
    prefix = prefix.strip('/')
    if prefix and append_slash:
        prefix += '/'
    pattern = '^' + prefix
    return pattern


def filter_modified_since(request, objects):
    """Filter DB objects based on 'changes-since' request parameter.

    Parse request for 'changes-since' parameter and get only the DB objects
    that have been updated after that time. Otherwise, return the non-deleted
    objects.

    """
    since = isoparse(request.GET.get("changes-since"))
    if since:
        modified_objs = objects.filter(updated__gte=since)
        if not modified_objs:
            raise faults.NotModified()
        return modified_objs
    else:
        return objects.filter(deleted=False)


def get_attribute(request, attribute, attr_type=None, required=True):
    value = request.get(attribute, None)
    if required and value is None:
        raise faults.BadRequest("Malformed request. Missing attribute '%s'." %
                                attribute)
    if attr_type is not None and value is not None\
       and not isinstance(value, attr_type):
        raise faults.BadRequest("Malformed request. Invalid '%s' field"
                                % attribute)
    return value
