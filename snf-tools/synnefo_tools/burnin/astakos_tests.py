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

"""
This is the burnin class that tests the Astakos functionality

"""

from kamaki.clients.compute import ComputeClient
from kamaki.clients import ClientError

from synnefo_tools.burnin.common import BurninTests, Proper


# pylint: disable=too-many-public-methods
class AstakosTestSuite(BurninTests):
    """Test Astakos functionality"""
    details = Proper(value=None)

    def test_001_unauthorized_access(self):
        """Test that access without a valid token fails"""
        false_token = "12345"
        self.info("Will use token %s", false_token)
        client = ComputeClient(self.clients.compute_url, false_token)
        client.CONNECTION_RETRY_LIMIT = self.clients.retry

        with self.assertRaises(ClientError) as cl_error:
            client.list_servers()
            self.assertEqual(cl_error.exception.status, 401)

    def test_002_name2uuid(self):
        """Test that usernames2uuids and uuids2usernames are complementary"""
        our_uuid = self._get_uuid()

        given_name = self.clients.astakos.get_usernames([our_uuid])
        self.info("uuids2usernames returned %s", given_name)
        self.assertIn(our_uuid, given_name)

        given_uuid = self.clients.astakos.get_uuids([given_name[our_uuid]])
        self.info("usernames2uuids returned %s", given_uuid)
        self.assertIn(given_name[our_uuid], given_uuid)

        self.assertEqual(given_uuid[given_name[our_uuid]], our_uuid)

    def test_005_authenticate(self):
        """Test astakos.authenticate"""
        astakos = self.clients.astakos
        self.details = astakos.authenticate()
        self.info('Check result integrity')
        self.assertIn('access', self.details)
        access = self.details['access']
        self.assertEqual(set(('user', 'token', 'serviceCatalog')), set(access))
        self.info('Top-level keys are correct')
        self.assertEqual(self.clients.token, access['token']['id'])
        self.info('Token is correct')
        self.assertEqual(
            set(['roles', 'name', 'id', 'roles_links']),
            set(astakos.user_info))
        self.info('User section is correct')

    def test_010_get_service_endpoints(self):
        """Test endpoints integrity"""
        scat = self.details['access']['serviceCatalog']
        types = (
            'compute', 'object-store', 'identity', 'account',
            'image', 'volume', 'network', 'astakos_weblogin',
            'admin', 'vmapi', 'astakos_auth')
        self.assertEqual(set(types), set([s['type'] for s in scat]))
        self.info('All expected endpoint types (and only them) found')

        astakos = self.clients.astakos
        for etype in types:
            endpoint = [s for s in scat
                        if s['type'] == etype][0]['endpoints'][0]
            self.assertEqual(endpoint, astakos.get_service_endpoints(etype))
        self.info('Endpoint call results match original results')
