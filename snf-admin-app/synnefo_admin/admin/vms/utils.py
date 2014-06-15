# Copyright (C) 2010-2014 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
import re
from collections import OrderedDict

from operator import or_

from django.core.urlresolvers import reverse
from django.db.models import Q

from synnefo.db.models import VirtualMachine, Network, IPAddressLog
from astakos.im.models import AstakosUser, ProjectMembership, Project
from astakos.im.user_utils import send_plain as send_email

from synnefo.logic import servers as servers_backend
from synnefo.logic.commands import validate_server_action

from eztables.views import DatatablesView

import django_filters

from synnefo_admin.admin.actions import (AdminAction, noop,
                                         has_permission_or_403)


def get_vm(query):
    try:
        id = query.translate(None, 'vm-')
    except Exception:
        id = query
    return VirtualMachine.objects.get(pk=int(id))


def get_flavor_info(vm):
    return ('CPU: ' + str(vm.flavor.cpu) + ', RAM: ' + str(vm.flavor.ram) +
            ', Disk size: ' + str(vm.flavor.disk) + ', Disk template: ' +
            str(vm.flavor.volume_type.disk_template))
