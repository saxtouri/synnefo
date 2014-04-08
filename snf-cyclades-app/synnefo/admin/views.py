# Copyright 2013 GRNET S.A. All rights reserved.
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

import logging
from django import http
from django.utils import simplejson as json
from django.conf import settings
from snf_django.lib import api
from snf_django.lib.api import utils, faults
from synnefo.db.models import Backend

from synnefo.admin import stats

logger = logging.getLogger(__name__)


@api.api_method(http_method='GET', user_required=False, token_required=False,
                logger=logger, serializations=['json'])
@api.allow_jsonp()
def get_public_stats(request):
    _stats = stats.get_public_stats()
    data = json.dumps(_stats)
    return http.HttpResponse(data, status=200, content_type='application/json')


@api.api_method(http_method='GET', user_required=True, token_required=True,
                logger=logger, serializations=['json'])
@api.user_in_groups(permitted_groups=settings.ADMIN_STATS_PERMITTED_GROUPS,
                    logger=logger)
def get_cyclades_stats(request):
    images = True
    backend = None
    if request.body:
        req = utils.get_json_body(request)
        req_stats = utils.get_attribute(req, "stats", required=True,
                                        attr_type=dict)
        # Check backend
        backend_id = utils.get_attribute(req_stats, "backend", required=False,
                                         attr_type=(basestring, int))
        if backend_id is not None:
            try:
                try:
                    backend_id = int(backend_id)
                    backend = Backend.objects.get(id=backend_id)
                except (ValueError, TypeError):
                    backend = Backend.objects.get(clustername=backend_id)
            except Backend.DoesNotExist:
                raise faults.BadRequest("Invalid backend '%s'" % backend_id)
        include_images = utils.get_attribute(req_stats, "images",
                                             required=False,
                                             attr_type=bool)
        if include_images is not None:
            images = include_images

    _stats = stats.get_cyclades_stats(backend=backend, clusters=True,
                                      servers=True, resources=True,
                                      networks=True, images=images)
    data = json.dumps(_stats)
    return http.HttpResponse(data, status=200, content_type='application/json')
