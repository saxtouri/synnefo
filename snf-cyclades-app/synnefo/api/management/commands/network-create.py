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

from synnefo.db.models import Network, Backend
from synnefo.logic.backend import create_network
from _common import validate_network_info

NETWORK_TYPES = ['PUBLIC_ROUTED', 'PRIVATE_MAC_FILTERED',
                 'PRIVATE_PHYSICAL_VLAN', 'CUSTOM_ROUTED',
                 'CUSTOM_BRIDGED']


class Command(BaseCommand):
    can_import_settings = True
    output_transaction = True

    help = "Create a new network"

    option_list = BaseCommand.option_list + (
        make_option('--name',
            dest='name',
            help="Name of network"),
        make_option('--owner',
            dest='owner',
            help="The owner of the network"),
        make_option('--subnet',
            dest='subnet',
            default=None,
            # required=True,
            help='Subnet of the network'),
        make_option('--gateway',
            dest='gateway',
            default=None,
            help='Gateway of the network'),
        make_option('--dhcp',
            dest='dhcp',
            action='store_true',
            default=False,
            help='Automatically assign IPs'),
        make_option('--public',
            dest='public',
            action='store_true',
            default=False,
            help='Network is public'),
        make_option('--type',
            dest='type',
            default='PRIVATE_MAC_FILTERED',
            choices=NETWORK_TYPES,
            help='Type of network. Choices: ' + ', '.join(NETWORK_TYPES)),
        make_option('--subnet6',
            dest='subnet6',
            default=None,
            help='IPv6 subnet of the network'),
        make_option('--gateway6',
            dest='gateway6',
            default=None,
            help='IPv6 gateway of the network'),
        make_option('--backend-id',
            dest='backend_id',
            default=None,
            help='ID of the backend that the network will be created. Only for'
                 ' public networks'),
        make_option('--link',
            dest='link',
            default=None,
            help="Connectivity link of the Network. None for default."),
        make_option('--mac-prefix',
            dest='mac_prefix',
            default=None,
            help="MAC prefix of the network. None for default")
        )

    def handle(self, *args, **options):
        if args:
            raise CommandError("Command doesn't accept any arguments")

        name = options['name']
        subnet = options['subnet']
        net_type = options['type']
        backend_id = options['backend_id']
        public = options['public']
        link = options['link']
        mac_prefix = options['mac_prefix']

        if not name:
            raise CommandError("Name is required")
        if not subnet:
            raise CommandError("Subnet is required")
        if public and not backend_id:
            raise CommandError("backend-id is required")
        if backend_id and not public:
            raise CommandError("Private networks must be created to"
                               " all backends")

        if mac_prefix and net_type == "PRIVATE_MAC_FILTERED":
            raise CommandError("Can not override MAC_FILTERED mac-prefix")
        if link and net_type == "PRIVATE_PHYSICAL_VLAN":
            raise CommandError("Can not override PHYSICAL_VLAN link")

        if backend_id:
            try:
                backend_id = int(backend_id)
                backend = Backend.objects.get(id=backend_id)
            except ValueError:
                raise CommandError("Invalid backend ID")
            except Backend.DoesNotExist:
                raise CommandError("Backend not found in DB")

        default_link, default_mac_prefix = net_resources(net_type)
        if not link:
            link = default_link
        if not mac_prefix:
            mac_prefix = default_mac_prefix

        subnet, gateway, subnet6, gateway6 = validate_network_info(options)

        if not link:
            raise CommandError("Can not create network. No connectivity link")

        network = Network.objects.create(
                name=name,
                userid=options['owner'],
                subnet=subnet,
                gateway=gateway,
                dhcp=options['dhcp'],
                type=net_type,
                public=public,
                link=link,
                mac_prefix=mac_prefix,
                gateway6=gateway6,
                subnet6=subnet6,
                state='PENDING')

        if public:
            # Create BackendNetwork only to the specified Backend
            network.create_backend_network(backend)
            create_network(network, backends=[backend])
        else:
            # Create BackendNetwork entries for all Backends
            network.create_backend_network()
            create_network(network)
