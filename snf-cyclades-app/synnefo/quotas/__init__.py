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

from django.utils import simplejson as json
from django.db import transaction
from django.db.models import Sum

from snf_django.lib.api import faults
from synnefo.db.models import (QuotaHolderSerial, VirtualMachine, Network,
                               IPAddress, Volume)

from synnefo.settings import (CYCLADES_SERVICE_TOKEN as ASTAKOS_TOKEN,
                              ASTAKOS_AUTH_URL)
from astakosclient import AstakosClient
from astakosclient import errors

import logging
log = logging.getLogger(__name__)


QUOTABLE_RESOURCES = [VirtualMachine, Network, IPAddress]


RESOURCES = [
    "cyclades.vm",
    "cyclades.total_cpu",
    "cyclades.cpu",
    "cyclades.disk",
    "cyclades.total_ram",
    "cyclades.ram",
    "cyclades.network.private",
    "cyclades.floating_ip",
]


class Quotaholder(object):
    _object = None

    @classmethod
    def get(cls):
        if cls._object is None:
            cls._object = AstakosClient(ASTAKOS_TOKEN,
                                        ASTAKOS_AUTH_URL,
                                        use_pool=True,
                                        retry=3,
                                        logger=log)
        return cls._object


class AstakosClientExceptionHandler(object):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.get("user")
        self.projects = kwargs.get("projects")

    def __enter__(self):
        pass

    def check_notFound(self):
        if not self.user or not self.projects:
            return
        try:
            qh = Quotaholder.get()
            user_quota = qh.service_get_quotas(self.user)
        except errors.AstakosClientException as e:
            log.exception("Unexpected error %s" % e.message)
            raise faults.InternalServerError("Unexpected error")

        user_quota = user_quota[self.user]
        for project in self.projects:
            try:
                user_quota[project]
            except KeyError:
                m = "User %s not in project %s" % (self.user, project)
                raise faults.BadRequest(m)

    def __exit__(self, exc_type, value, traceback):
        if value is not None:  # exception
            if not isinstance(value, errors.AstakosClientException):
                return False  # reraise
            if exc_type is errors.QuotaLimit:
                msg, details = render_overlimit_exception(value)
                raise faults.OverLimit(msg, details=details)
            if exc_type is errors.NotFound:
                self.check_notFound()

            log.exception("Unexpected error %s" % value.message)
            raise faults.InternalServerError("Unexpected error")


def issue_commission(resource, action, name="", force=False, auto_accept=False,
                     action_fields=None):
    """Issue a new commission to the quotaholder.

    Issue a new commission to the quotaholder, and create the
    corresponing QuotaHolderSerial object in DB.

    """

    provisions = get_commission_info(resource=resource, action=action,
                                     action_fields=action_fields)

    if provisions is None:
        return None

    user = resource.userid
    source = resource.project

    qh = Quotaholder.get()
    if action == "REASSIGN":
        try:
            from_project = action_fields["from_project"]
            to_project = action_fields["to_project"]
        except KeyError:
            raise Exception("Missing project attribute.")

        projects = [from_project, to_project]
        with AstakosClientExceptionHandler(user=user, projects=projects):
            serial = qh.issue_resource_reassignment(user,
                                                    from_project, to_project,
                                                    provisions, name=name,
                                                    force=force,
                                                    auto_accept=auto_accept)
    else:
        with AstakosClientExceptionHandler(user=user, projects=[source]):
            serial = qh.issue_one_commission(user, source,
                                             provisions, name=name,
                                             force=force,
                                             auto_accept=auto_accept)

    if not serial:
        raise Exception("No serial")

    serial_info = {"serial": serial}
    if auto_accept:
        serial_info["pending"] = False
        serial_info["accept"] = True
        serial_info["resolved"] = True

    serial = QuotaHolderSerial.objects.create(**serial_info)

    # Correlate the serial with the resource. Resolved serials are not
    # attached to resources
    if not auto_accept:
        resource.serial = serial
        resource.save()

    return serial


def accept_resource_serial(resource, strict=True):
    serial = resource.serial
    assert serial.pending or serial.accept, "%s can't be accepted" % serial
    log.debug("Accepting serial %s of resource %s", serial, resource)
    _resolve_commissions(accept=[serial.serial], strict=strict)
    resource.serial = None
    resource.save()
    return resource


def reject_resource_serial(resource, strict=True):
    serial = resource.serial
    assert serial.pending or not serial.accept, "%s can't be rejected" % serial
    log.debug("Rejecting serial %s of resource %s", serial, resource)
    _resolve_commissions(reject=[serial.serial], strict=strict)
    resource.serial = None
    resource.save()
    return resource


def _resolve_commissions(accept=None, reject=None, strict=True):
    if accept is None:
        accept = []
    if reject is None:
        reject = []

    qh = Quotaholder.get()
    with AstakosClientExceptionHandler():
        response = qh.resolve_commissions(accept, reject)

    accepted = response.get("accepted", [])
    rejected = response.get("rejected", [])

    if accepted:
        QuotaHolderSerial.objects.filter(serial__in=accepted).update(
            accept=True, pending=False, resolved=True)
    if rejected:
        QuotaHolderSerial.objects.filter(serial__in=rejected).update(
            accept=False, pending=False, resolved=True)

    if strict:
        failed = response["failed"]
        if failed:
            log.error("Unexpected error while resolving commissions: %s",
                      failed)

    return response


def reconcile_resolve_commissions(accept=None, reject=None, strict=True):
    response = _resolve_commissions(accept=accept,
                                    reject=reject,
                                    strict=strict)
    affected = response.get("accepted", []) + response.get("rejected", [])
    for resource in QUOTABLE_RESOURCES:
        resource.objects.filter(serial__in=affected).update(serial=None)


def resolve_pending_commissions():
    """Resolve quotaholder pending commissions.

    Get pending commissions from the quotaholder and resolve them
    to accepted and rejected, according to the state of the
    QuotaHolderSerial DB table. A pending commission in the quotaholder
    can exist in the QuotaHolderSerial table and be either accepted or
    rejected, or cannot exist in this table, so it is rejected.

    """

    qh_pending = get_quotaholder_pending()
    if not qh_pending:
        return ([], [])

    qh_pending.sort()
    min_ = qh_pending[0]

    serials = QuotaHolderSerial.objects.filter(serial__gte=min_, pending=False)
    accepted = serials.filter(accept=True).values_list('serial', flat=True)
    accepted = filter(lambda x: x in qh_pending, accepted)

    rejected = list(set(qh_pending) - set(accepted))

    return (accepted, rejected)


def get_quotaholder_pending():
    qh = Quotaholder.get()
    pending_serials = qh.get_pending_commissions()
    return pending_serials


def render_overlimit_exception(e):
    resource_name = {"vm": "Virtual Machine",
                     "cpu": "CPU",
                     "ram": "RAM",
                     "network.private": "Private Network",
                     "floating_ip": "Floating IP address"}
    details = json.loads(e.details)
    data = details['overLimit']['data']
    usage = data["usage"]
    limit = data["limit"]
    available = limit - usage
    provision = data['provision']
    requested = provision['quantity']
    resource = provision['resource']
    res = resource.replace("cyclades.", "", 1)
    try:
        resource = resource_name[res]
    except KeyError:
        resource = res

    msg = "Resource Limit Exceeded for your account."
    details = "Limit for resource '%s' exceeded for your account."\
              " Available: %s, Requested: %s"\
              % (resource, available, requested)
    return msg, details


@transaction.commit_on_success
def issue_and_accept_commission(resource, action="BUILD", action_fields=None):
    """Issue and accept a commission to Quotaholder.

    This function implements the Commission workflow, and must be called
    exactly after and in the same transaction that created/updated the
    resource. The workflow that implements is the following:
    0) Resolve previous unresolved commission if exists
    1) Issue commission, get a serial and correlate it with the resource
    2) Store the serial in DB as a serial to accept
    3) COMMIT!
    4) Accept commission to QH

    """
    commission_reason = ("client: api, resource: %s, action: %s"
                         % (resource, action))
    serial = handle_resource_commission(resource=resource, action=action,
                                        action_fields=action_fields,
                                        commission_name=commission_reason)

    if serial is None:
        return

    # Mark the serial as one to accept and associate it with the resource
    serial.pending = False
    serial.accept = True
    serial.save()
    transaction.commit()

    try:
        # Accept the commission to quotaholder
        accept_resource_serial(resource)
    except:
        # Do not crash if we can not accept commission to Quotaholder. Quotas
        # have already been reserved and the resource already exists in DB.
        # Just log the error
        log.exception("Failed to accept commission: %s", resource.serial)


def get_commission_info(resource, action, action_fields=None):
    if isinstance(resource, VirtualMachine):
        flavor = resource.flavor
        resources = {"cyclades.vm": 1,
                     "cyclades.total_cpu": flavor.cpu,
                     "cyclades.total_ram": flavor.ram << 20}
        online_resources = {"cyclades.cpu": flavor.cpu,
                            "cyclades.ram": flavor.ram << 20}
        if action == "BUILD":
            new_volumes = resource.volumes.filter(status="CREATING")
            new_volumes_size = new_volumes.aggregate(Sum("size"))["size__sum"]
            resources["cyclades.disk"] = int(new_volumes_size) << 30
            resources.update(online_resources)
            return resources
        if action == "START":
            if resource.operstate == "STOPPED":
                return online_resources
            else:
                return None
        elif action == "STOP":
            if resource.operstate in ["STARTED", "BUILD", "ERROR"]:
                return reverse_quantities(online_resources)
            else:
                return None
        elif action == "REBOOT":
            if resource.operstate == "STOPPED":
                return online_resources
            else:
                return None
        elif action == "DESTROY":
            volumes = resource.volumes.filter(deleted=False)
            volumes_size = volumes.aggregate(Sum("size"))["size__sum"]
            if volumes_size is None:
                volumes_size = 0
            resources["cyclades.disk"] = int(volumes_size) << 30
            if resource.operstate in ["STARTED", "BUILD", "ERROR"]:
                resources.update(online_resources)
            return reverse_quantities(resources)
        elif action == "RESIZE" and action_fields:
            beparams = action_fields.get("beparams")
            cpu = beparams.get("vcpus", flavor.cpu)
            ram = beparams.get("maxmem", flavor.ram)
            return {"cyclades.total_cpu": cpu - flavor.cpu,
                    "cyclades.total_ram": (ram - flavor.ram) << 20}
        elif action == "REASSIGN":
            if resource.operstate in ["STARTED", "BUILD", "ERROR"]:
                resources.update(online_resources)
            return resources
        elif action in ["ATTACH_VOLUME", "DETACH_VOLUME"]:
            if action_fields is not None:
                volumes_changes = action_fields.get("disks")
                if volumes_changes is not None:
                    size_delta = get_volumes_size_delta(volumes_changes)
                    if size_delta:
                        return {"cyclades.disk": size_delta << 30}
        else:
            #["CONNECT", "DISCONNECT", "SET_FIREWALL_PROFILE"]:
            return None
    elif isinstance(resource, Network):
        resources = {"cyclades.network.private": 1}
        if action == "BUILD":
            return resources
        elif action == "DESTROY":
            return reverse_quantities(resources)
        elif action == "REASSIGN":
            return resources
    elif isinstance(resource, IPAddress):
        if resource.floating_ip:
            resources = {"cyclades.floating_ip": 1}
            if action == "BUILD":
                return resources
            elif action == "DESTROY":
                return reverse_quantities(resources)
            elif action == "REASSIGN":
                return resources
        else:
            return None
    elif isinstance(resource, Volume):
        size = resource.size
        resources = {"cyclades.disk": size << 30}
        if resource.status == "CREATING" and action == "BUILD":
            return resources
        elif action == "DESTROY":
            reverse_quantities(resources)
        else:
            return None


def get_volumes_size_delta(volumes_changes):
    """Compute the total change in the size of volumes"""
    size_delta = 0
    for vchange in volumes_changes:
        action, db_volume, info = vchange
        if action == "add":
            size_delta += int(db_volume.size)
        elif action == "remove":
            size_delta -= int(db_volume.size)
        elif action == "modify":
            size_delta += info.get("size_delta", 0)
        else:
            raise ValueError("Unknown volume action '%s'" % action)
    return size_delta


def reverse_quantities(resources):
    return dict((r, -s) for r, s in resources.items())


def handle_resource_commission(resource, action, commission_name,
                               force=False, auto_accept=False,
                               action_fields=None):
    """Handle a issuing of a commission for a resource.

    Create a new commission for a resource based on the action that
    is performed. If the resource has a previous pending commission,
    resolved it before issuing the new one.

    """
    # Try to resolve previous serial:
    # If action is DESTROY, we must always reject the previous commission,
    # since multiple DESTROY actions are allowed in the same resource (e.g. VM)
    # The one who succeeds will be finally accepted, and all other will be
    # rejected
    force = force or (action == "DESTROY")
    resolve_resource_commission(resource, force=force)

    serial = issue_commission(resource, action, name=commission_name,
                              force=force, auto_accept=auto_accept,
                              action_fields=action_fields)
    return serial


class ResolveError(Exception):
    pass


def resolve_resource_commission(resource, force=False):
    serial = resource.serial
    if serial is None or serial.resolved:
        return
    if serial.pending and not force:
        m = "Could not resolve commission: serial %s is undecided" % serial
        raise ResolveError(m)
    log.warning("Resolving pending commission: %s", serial)
    if not serial.pending and serial.accept:
        accept_resource_serial(resource)
    else:
        reject_resource_serial(resource)
