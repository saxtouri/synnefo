# Copyright 2012 GRNET S.A. All rights reserved.
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

from optparse import make_option

from django.core.management.base import BaseCommand, CommandError
from synnefo.management.common import (format_bool, filter_results, UserCache,
                                       Omit)
from synnefo.db.models import Network
from synnefo.management.common import pprint_table

FIELDS = Network._meta.get_all_field_names()


class Command(BaseCommand):
    help = "List networks"

    option_list = BaseCommand.option_list + (
        make_option('-c',
            action='store_true',
            dest='csv',
            default=False,
            help="Use pipes to separate values"),
        make_option('--deleted',
            action='store_true',
            dest='deleted',
            default=False,
            help="Include deleted networks"),
        make_option('--public',
            action='store_true',
            dest='public',
            default=False,
            help="List only public networks"),
        make_option('--user',
            dest='user',
            help="List only networks of the specified user"
                 " (uuid or display name"),
        make_option('--ipv6',
            action='store_true',
            dest='ipv6',
            default=False,
            help="Show IPv6 information of the network"),
        make_option('--filter-by',
            dest='filter_by',
            help="Filter results. Comma seperated list of key 'cond' val pairs"
                 " that displayed entries must satisfy. e.g."
                 " --filter-by \"name=Network-1,link!=prv0\"."
                 " Available keys are: %s" % ", ".join(FIELDS)),
        make_option('--displayname',
            action='store_true',
            dest='displayname',
            default=False,
            help="Display both uuid and display name"),
        )

    def handle(self, *args, **options):
        if args:
            raise CommandError("Command doesn't accept any arguments")

        ucache = UserCache()

        if options['deleted']:
            networks = Network.objects.all()
        else:
            networks = Network.objects.filter(deleted=False)

        if options['public']:
            networks = networks.filter(public=True)

        user = options['user']
        if user:
            if '@' in user:
                user = ucache.get_uuid(user)
            networks = networks.filter(userid=user)

        filter_by = options['filter_by']
        if filter_by:
            networks = filter_results(networks, filter_by)

        displayname = options['displayname']

        headers = filter(lambda x: x is not Omit,
                         ['id',
                          'name',
                          'flavor',
                          'owner_uuid',
                          'owner_name' if displayname else Omit,
                          'mac_prefix',
                          'dhcp',
                          'state',
                          'link',
                          'vms',
                          'public',
                          ])

        if options['ipv6']:
            headers.extend(['IPv6 Subnet', 'IPv6 Gateway'])
        else:
            headers.extend(['IPv4 Subnet', 'IPv4 Gateway'])

        uuids = list(set([network.userid for network in networks]))
        ucache.fetch_names(uuids)

        table = []
        for network in networks.order_by("id"):
            uuid = network.userid
            if displayname:
                dname = ucache.get_name(uuid)

            fields = filter(lambda x: x is not Omit,
                            [str(network.id),
                             network.name,
                             network.flavor,
                             uuid or '-',
                             dname or '-' if displayname else Omit,
                             network.mac_prefix or '-',
                             str(network.dhcp),
                             network.state,
                             network.link or '-',
                             str(network.machines.count()),
                             format_bool(network.public),
                             ])

            if options['ipv6']:
                fields.extend([network.subnet6 or '', network.gateway6 or ''])
            else:
                fields.extend([network.subnet, network.gateway or ''])
            table.append(fields)

        separator = " | " if options['csv'] else None
        pprint_table(self.stdout, table, headers, separator)
