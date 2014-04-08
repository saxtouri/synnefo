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
This is the burnin class that tests the Pithos functionality

"""

import os
import random
import tempfile
from datetime import datetime

from synnefo_tools.burnin.common import BurninTests, Proper, \
    QPITHOS, QADD, QREMOVE
from kamaki.clients import ClientError


# pylint: disable=too-many-public-methods
class PithosTestSuite(BurninTests):
    """Test Pithos functionality"""
    containers = Proper(value=None)
    created_container = Proper(value=None)
    now_unformated = Proper(value=datetime.utcnow())

    def test_005_account_head(self):
        """HEAD on pithos account"""
        self._set_pithos_account(self._get_uuid())
        pithos = self.clients.pithos
        resp = pithos.account_head()
        self.assertEqual(resp.status_code, 204)
        self.info('Returns 204')

        resp = pithos.account_head(until='1000000000')
        self.assertEqual(resp.status_code, 204)
        datestring = unicode(resp.headers['x-account-until-timestamp'])
        self.assertEqual(u'Sun, 09 Sep 2001 01:46:40 GMT', datestring)
        self.assertTrue(any([
            h.startswith('x-account-policy-quota') for h in resp.headers]))
        self.info('Until and account policy quota exist')

        for date_format in pithos.DATE_FORMATS:
            now_formated = self.now_unformated.strftime(date_format)
            resp1 = pithos.account_head(
                if_modified_since=now_formated, success=(204, 304, 412))
            resp2 = pithos.account_head(
                if_unmodified_since=now_formated, success=(204, 304, 412))
            self.assertNotEqual(resp1.status_code, resp2.status_code)
        self.info('If_(un)modified_since is OK')

    def test_010_account_get(self):  # pylint: disable=too-many-locals
        """Test account_get"""
        self.info('Preparation')
        pithos = self.clients.pithos
        for i in range(1, 3):
            cont_name = "cont%s_%s%s" % (
                i, self.run_id or 0, random.randint(1000, 9999))
            self._create_pithos_container(cont_name)
        pithos.container, obj = cont_name, 'shared_file'
        pithos.create_object(obj)
        pithos.set_object_sharing(obj, read_permission='*')
        self.info('Created object /%s/%s' % (cont_name, obj))

        resp = pithos.list_containers()
        full_len = len(resp)
        self.assertTrue(full_len > 2)
        self.info('Normal use is OK')

        resp = pithos.account_get(limit=1)
        self.assertEqual(len(resp.json), 1)
        self.info('Limit works')

        resp = pithos.account_get(marker='cont')
        cont1, cont3 = resp.json[0], resp.json[2]
        self.info('Marker works')

        resp = pithos.account_get(limit=2, marker='cont')
        conames = [container['name'] for container in resp.json if (
            container['name'].lower().startswith('cont'))]
        self.assertTrue(cont1['name'] in conames)
        self.assertFalse(cont3['name'] in conames)
        self.info('Marker-limit combination works')

        resp = pithos.account_get(show_only_shared=True)
        self.assertTrue(cont_name in [c['name'] for c in resp.json])
        self.info('Show-only-shared works')

        resp = pithos.account_get(until=1342609206.0)
        self.assertTrue(len(resp.json) <= full_len)
        self.info('Until works')

        for date_format in pithos.DATE_FORMATS:
            now_formated = self.now_unformated.strftime(date_format)
            resp1 = pithos.account_get(
                if_modified_since=now_formated, success=(200, 304, 412))
            resp2 = pithos.account_get(
                if_unmodified_since=now_formated, success=(200, 304, 412))
            self.assertNotEqual(resp1.status_code, resp2.status_code)
        self.info('If_(un)modified_since is OK')

    def test_015_account_post(self):
        """Test account_post"""
        pithos = self.clients.pithos
        resp = pithos.account_post()
        self.assertEqual(resp.status_code, 202)
        self.info('Status code is OK')

        rand_num = '%s%s' % (self.run_id or 0, random.randint(1000, 9999))
        grp_name = 'grp%s' % rand_num

        uuid1, uuid2 = pithos.account, 'invalid-user-uuid-%s' % rand_num
        self.assertRaises(
            ClientError, pithos.set_account_group, grp_name, [uuid1, uuid2])
        self.info('Invalid uuid is handled correctly')

        pithos.set_account_group(grp_name, [uuid1])
        resp = pithos.get_account_group()
        self.assertEqual(resp['x-account-group-' + grp_name], '%s' % uuid1)
        self.info('Account group is OK')
        pithos.del_account_group(grp_name)
        resp = pithos.get_account_group()
        self.assertTrue('x-account-group-' + grp_name not in resp)
        self.info('Removed account group')

        mprefix = 'meta%s' % rand_num
        pithos.set_account_meta({
            mprefix + '1': 'v1', mprefix + '2': 'v2'})
        resp = pithos.get_account_meta()
        self.assertEqual(resp['x-account-meta-' + mprefix + '1'], 'v1')
        self.assertEqual(resp['x-account-meta-' + mprefix + '2'], 'v2')
        self.info('Account meta is OK')

        pithos.del_account_meta(mprefix + '1')
        resp = pithos.get_account_meta()
        self.assertTrue('x-account-meta-' + mprefix + '1' not in resp)
        self.assertTrue('x-account-meta-' + mprefix + '2' in resp)
        self.info('Selective removal of account meta is OK')

        pithos.del_account_meta(mprefix + '2')
        resp = pithos.get_account_meta()
        self.assertTrue('x-account-meta-' + mprefix + '2' not in resp)
        self.info('Temporary account meta are removed')

    def test_020_container_head(self):
        """Test container HEAD"""
        pithos = self.clients.pithos
        resp = pithos.container_head()
        self.assertEqual(resp.status_code, 204)
        self.info('Status code is OK')

        resp = pithos.container_head(until=1000000, success=(204, 404))
        self.assertEqual(resp.status_code, 404)
        self.info('Until works')

        for date_format in pithos.DATE_FORMATS:
            now_formated = self.now_unformated.strftime(date_format)
            resp1 = pithos.container_head(
                if_modified_since=now_formated, success=(204, 304, 412))
            resp2 = pithos.container_head(
                if_unmodified_since=now_formated, success=(204, 304, 412))
            self.assertNotEqual(resp1.status_code, resp2.status_code)

        k = 'metakey%s' % random.randint(1000, 9999)
        pithos.set_container_meta({k: 'our value'})
        resp = pithos.get_container_meta()
        k = 'x-container-meta-%s' % k
        self.assertIn(k, resp)
        self.assertEqual('our value', resp[k])
        self.info('Container meta exists')

    def test_051_list_containers(self):
        """Test container list actually returns containers"""
        self.containers = self._get_list_of_containers()
        self.assertGreater(len(self.containers), 0)

    def test_052_unique_containers(self):
        """Test if containers have unique names"""
        names = [n['name'] for n in self.containers]
        names = sorted(names)
        self.assertEqual(sorted(list(set(names))), names)

    def test_053_create_container(self):
        """Test creating a new container"""
        names = [n['name'] for n in self.containers]
        while True:
            rand_num = random.randint(1000, 9999)
            rand_name = "%s%s" % (self.run_id or 0, rand_num)
            self.info("Trying container name %s", rand_name)
            if rand_name not in names:
                break
            self.info("Container name %s already exists", rand_name)
        # Create container
        self._create_pithos_container(rand_name)
        # Verify that container is created
        containers = self._get_list_of_containers()
        self.info("Verify that container %s is created", rand_name)
        names = [n['name'] for n in containers]
        self.assertIn(rand_name, names)
        # Keep the name of the container so we can remove it
        # at cleanup phase, if something goes wrong.
        self.created_container = rand_name

    def test_054_upload_file(self):
        """Test uploading a txt file to Pithos"""
        # Create a tmp file
        with tempfile.TemporaryFile(dir=self.temp_directory) as fout:
            fout.write("This is a temp file")
            fout.seek(0, 0)
            # Upload the file,
            # The container is the one choosen during the `create_container'
            self.clients.pithos.upload_object("test.txt", fout)
            # Verify quotas
            size = os.fstat(fout.fileno()).st_size
            changes = \
                {self._get_uuid(): [(QPITHOS, QADD, size, None)]}
            self._check_quotas(changes)

    def test_055_download_file(self):
        """Test downloading the file from Pithos"""
        # Create a tmp directory to save the file
        with tempfile.TemporaryFile(dir=self.temp_directory) as fout:
            self.clients.pithos.download_object("test.txt", fout)
            # Now read the file
            fout.seek(0, 0)
            contents = fout.read()
            # Compare results
            self.info("Comparing contents with the uploaded file")
            self.assertEqual(contents, "This is a temp file")

    def test_056_remove(self):
        """Test removing files and containers from Pithos"""
        self.info("Removing the file %s from container %s",
                  "test.txt", self.created_container)
        # The container is the one choosen during the `create_container'
        content_length = \
            self.clients.pithos.get_object_info("test.txt")['content-length']
        self.clients.pithos.del_object("test.txt")

        # Verify quotas
        changes = \
            {self._get_uuid(): [(QPITHOS, QREMOVE, content_length, None)]}
        self._check_quotas(changes)

        self.info("Removing the container %s", self.created_container)
        self.clients.pithos.purge_container()

        # List containers
        containers = self._get_list_of_containers()
        self.info("Check that the container %s has been deleted",
                  self.created_container)
        names = [n['name'] for n in containers]
        self.assertNotIn(self.created_container, names)
        # We successfully deleted our container, no need to do it
        # in our clean up phase
        self.created_container = None

    @classmethod
    def tearDownClass(cls):  # noqa
        """Clean up"""
        if cls.created_container is not None:
            cls.clients.pithos.del_container(delimiter='/')
            cls.clients.pithos.purge_container()
