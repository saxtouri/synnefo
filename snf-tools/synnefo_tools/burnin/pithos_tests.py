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
from tempfile import NamedTemporaryFile

from synnefo_tools.burnin.common import BurninTests, Proper, \
    QPITHOS, QADD, QREMOVE
from kamaki.clients import ClientError


def sample_block(fid, block):
    """Read a block from fid"""
    block_size = 4 * 1024 * 1024
    fid.seek(block * block_size)
    chars = [fid.read(1)]
    fid.seek(block_size / 2, 1)
    chars.append(fid.read(1))
    fid.seek((block + 1) * block_size - 1)
    chars.append(fid.read(1))
    return chars


# pylint: disable=too-many-public-methods
class PithosTestSuite(BurninTests):
    """Test Pithos functionality"""
    containers = Proper(value=None)
    created_container = Proper(value=None)
    now_unformated = Proper(value=datetime.utcnow())
    obj_metakey = Proper(value=None)
    large_file = Proper(value=None)

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

        self.obj_metakey = 'metakey%s' % random.randint(1000, 9999)
        obj = 'object_with_meta'
        pithos.create_object(obj)
        pithos.set_object_meta(obj, {self.obj_metakey: 'our value'})
        resp = pithos.get_container_object_meta()
        self.assertIn('x-container-object-meta', resp)
        self.assertIn(
            self.obj_metakey, resp['x-container-object-meta'].lower())
        self.info('Container object meta exists')

    def test_025_container_get(self):
        """Test container GET"""
        pithos = self.clients.pithos

        resp = pithos.container_get()
        self.assertEqual(resp.status_code, 200)
        self.info('Status code is OK')

        full_len = len(resp.json)
        self.assertGreater(full_len, 0)
        self.info('There are enough (%s) containers' % full_len)

        obj1 = 'test%s' % random.randint(1000, 9999)
        pithos.create_object(obj1)
        obj2 = 'test%s' % random.randint(1000, 9999)
        pithos.create_object(obj2)
        obj3 = 'another%s.test' % random.randint(1000, 9999)
        pithos.create_object(obj3)

        resp = pithos.container_get(prefix='test')
        self.assertTrue(len(resp.json) > 1)
        test_objects = [o for o in resp.json if o['name'].startswith('test')]
        self.assertEqual(len(resp.json), len(test_objects))
        self.info('Prefix is OK')

        resp = pithos.container_get(limit=1)
        self.assertEqual(len(resp.json), 1)
        self.info('Limit is OK')

        resp = pithos.container_get(marker=obj3[:-5])
        self.assertTrue(len(resp.json) > 1)
        aoobjects = [obj for obj in resp.json if obj['name'] > obj3[:-5]]
        self.assertEqual(len(resp.json), len(aoobjects))
        self.info('Marker is OK')

        resp = pithos.container_get(prefix=obj3, delimiter='.')
        self.assertTrue(full_len > len(resp.json))
        self.info('Delimiter is OK')

        resp = pithos.container_get(path='/')
        full_len += 3
        self.assertEqual(full_len, len(resp.json))
        self.info('Path is OK')

        resp = pithos.container_get(format='xml')
        self.assertEqual(resp.text.split()[4],
                         'name="' + pithos.container + '">')
        self.info('Format is OK')

        resp = pithos.container_get(meta=[self.obj_metakey, ])
        self.assertTrue(len(resp.json) > 0)
        self.info('Meta is OK')

        resp = pithos.container_get(show_only_shared=True)
        self.assertTrue(len(resp.json) < full_len)
        self.info('Show-only-shared is OK')

        try:
            resp = pithos.container_get(until=1000000000)
            datestring = unicode(resp.headers['x-account-until-timestamp'])
            self.assertEqual(u'Sun, 09 Sep 2001 01:46:40 GMT', datestring)
            self.info('Until is OK')
        except ClientError:
            pass

    def test_030_container_put(self):
        """Test container PUT"""
        pithos = self.clients.pithos
        pithos.container = 'cont%s%s' % (
            self.run_id or 0, random.randint(1000, 9999))
        self.temp_containers.append(pithos.container)

        resp = pithos.create_container()
        self.assertTrue(isinstance(resp, dict))

        resp = pithos.get_container_limit(pithos.container)
        cquota = resp.values()[0]
        newquota = 2 * int(cquota)
        self.info('Limit is OK')
        pithos.del_container()

        resp = pithos.create_container(sizelimit=newquota)
        self.assertTrue(isinstance(resp, dict))

        resp = pithos.get_container_limit(pithos.container)
        xquota = int(resp.values()[0])
        self.assertEqual(newquota, xquota)
        self.info('Can set container limit')
        pithos.del_container()

        resp = pithos.create_container(versioning='auto')
        self.assertTrue(isinstance(resp, dict))

        resp = pithos.get_container_versioning(pithos.container)
        nvers = resp.values()[0]
        self.assertEqual('auto', nvers)
        self.info('Versioning=auto is OK')
        pithos.del_container()

        resp = pithos.container_put(versioning='none')
        self.assertEqual(resp.status_code, 201)

        resp = pithos.get_container_versioning(pithos.container)
        nvers = resp.values()[0]
        self.assertEqual('none', nvers)
        self.info('Versioning=none is OK')
        pithos.del_container()

        resp = pithos.create_container(metadata={'m1': 'v1', 'm2': 'v2'})
        self.assertTrue(isinstance(resp, dict))

        resp = pithos.get_container_meta(pithos.container)
        self.assertTrue('x-container-meta-m1' in resp)
        self.assertEqual(resp['x-container-meta-m1'], 'v1')
        self.assertTrue('x-container-meta-m2' in resp)
        self.assertEqual(resp['x-container-meta-m2'], 'v2')

        resp = pithos.container_put(metadata={'m1': '', 'm2': 'v2a'})
        self.assertEqual(resp.status_code, 202)

        resp = pithos.get_container_meta(pithos.container)
        self.assertTrue('x-container-meta-m1' not in resp)
        self.assertTrue('x-container-meta-m2' in resp)
        self.assertEqual(resp['x-container-meta-m2'], 'v2a')
        self.info('Container meta is OK')

        pithos.del_container_meta(pithos.container)

    # pylint: disable=too-many-statements
    def test_035_container_post(self):
        """Test container POST"""
        pithos = self.clients.pithos

        resp = pithos.container_post()
        self.assertEqual(resp.status_code, 202)
        self.info('Status is OK')

        pithos.set_container_meta({'m1': 'v1', 'm2': 'v2'})
        resp = pithos.get_container_meta(pithos.container)
        self.assertTrue('x-container-meta-m1' in resp)
        self.assertEqual(resp['x-container-meta-m1'], 'v1')
        self.assertTrue('x-container-meta-m2' in resp)
        self.assertEqual(resp['x-container-meta-m2'], 'v2')
        self.info('Set metadata works')

        resp = pithos.del_container_meta('m1')
        resp = pithos.set_container_meta({'m2': 'v2a'})
        resp = pithos.get_container_meta(pithos.container)
        self.assertTrue('x-container-meta-m1' not in resp)
        self.assertTrue('x-container-meta-m2' in resp)
        self.assertEqual(resp['x-container-meta-m2'], 'v2a')
        self.info('Delete metadata works')

        resp = pithos.get_container_limit(pithos.container)
        cquota = resp.values()[0]
        newquota = 2 * int(cquota)
        resp = pithos.set_container_limit(newquota)
        resp = pithos.get_container_limit(pithos.container)
        xquota = int(resp.values()[0])
        self.assertEqual(newquota, xquota)
        self.info('Set quota works')

        pithos.set_container_versioning('auto')
        resp = pithos.get_container_versioning(pithos.container)
        nvers = resp.values()[0]
        self.assertEqual('auto', nvers)
        pithos.set_container_versioning('none')
        resp = pithos.get_container_versioning(pithos.container)
        nvers = resp.values()[0]
        self.assertEqual('none', nvers)
        self.info('Set versioning works')

        named_file = self._create_large_file(1024 * 1024 * 100)
        self.large_file = named_file
        self.info('Created file %s of 100 MB' % named_file.name)

        pithos.create_directory('dir')
        self.info('Upload the file ...')
        resp = pithos.upload_object('/dir/sample.file', named_file)
        for term in ('content-length', 'content-type', 'x-object-version'):
            self.assertTrue(term in resp)
        resp = pithos.get_object_info('/dir/sample.file')
        self.assertTrue(int(resp['content-length']) > 100000000)
        self.info('Made remote directory /dir and object /dir/sample.file')

        # TODO: What is tranfer_encoding? What should I check about it?

        obj = 'object_with_meta'
        pithos.container = self.temp_containers[-2]
        resp = pithos.object_post(
            obj, update='False', metadata={'newmeta': 'newval'})

        resp = pithos.get_object_info(obj)
        self.assertTrue('x-object-meta-newmeta' in resp)
        self.assertFalse('x-object-meta-%s' % self.obj_metakey not in resp)
        self.info('Metadata with update=False works')

    def test_040_container_delete(self):
        """Test container DELETE"""
        pithos = self.clients.pithos

        resp = pithos.container_delete(success=409)
        self.assertEqual(resp.status_code, 409)
        self.assertRaises(ClientError, pithos.container_delete)
        self.info('Successfully failed to delete non-empty container')

        resp = pithos.container_delete(until='1000000000')
        self.assertEqual(resp.status_code, 204)
        self.info('Successfully failed to delete old-timestamped container')

        obj_names = [o['name'] for o in pithos.container_get().json]
        pithos.del_container(delimiter='/')
        resp = pithos.container_get()
        self.assertEqual(len(resp.json), 0)
        self.info('Successfully emptied container')

        for obj in obj_names:
            resp = pithos.get_object_versionlist(obj)
            self.assertTrue(len(resp) > 0)
        self.info('Versions are still there')

        pithos.purge_container()
        for obj in obj_names:
            self.assertRaises(ClientError, pithos.get_object_versionlist, obj)
        self.info('Successfully purged container')

        self.temp_containers.remove(pithos.container)
        pithos.container = self.temp_containers[-1]

    def test_045_object_head(self):
        """Test object HEAD"""
        pithos = self.clients.pithos

        obj = 'dir/sample.file'
        resp = pithos.object_head(obj)
        self.assertEqual(resp.status_code, 200)
        self.info('Status code is OK')
        etag = resp.headers['etag']
        real_version = resp.headers['x-object-version']

        self.assertRaises(ClientError, pithos.object_head, obj, version=-10)
        resp = pithos.object_head(obj, version=real_version)
        self.assertEqual(resp.headers['x-object-version'], real_version)
        self.info('Version works')

        resp = pithos.object_head(obj, if_etag_match=etag)
        self.assertEqual(resp.status_code, 200)
        self.info('if-etag-match is OK')

        resp = pithos.object_head(
            obj, if_etag_not_match=etag, success=(200, 412, 304))
        self.assertNotEqual(resp.status_code, 200)
        self.info('if-etag-not-match works')

        resp = pithos.object_head(
            obj, version=real_version, if_etag_match=etag, success=200)
        self.assertEqual(resp.status_code, 200)
        self.info('Version with if-etag-match works')

        for date_format in pithos.DATE_FORMATS:
            now_formated = self.now_unformated.strftime(date_format)
            resp1 = pithos.object_head(
                obj, if_modified_since=now_formated, success=(200, 304, 412))
            resp2 = pithos.object_head(
                obj, if_unmodified_since=now_formated, success=(200, 304, 412))
            self.assertNotEqual(resp1.status_code, resp2.status_code)
        self.info('if-(un)modified-since works')

    # pylint: disable=too-many-locals
    def test_050_object_get(self):
        """Test object GET"""
        pithos = self.clients.pithos
        obj = 'dir/sample.file'

        resp = pithos.object_get(obj)
        self.assertEqual(resp.status_code, 200)
        self.info('Status code is OK')

        osize = int(resp.headers['content-length'])
        etag = resp.headers['etag']

        resp = pithos.object_get(obj, hashmap=True)
        self.assertEqual(
            set(('hashes', 'block_size', 'block_hash', 'bytes')),
            set(resp.json))
        self.info('Hashmap works')
        hash0 = resp.json['hashes'][0]

        resp = pithos.object_get(obj, format='xml', hashmap=True)
        self.assertTrue(resp.text.split('hash>')[1].startswith(hash0))
        self.info('Hashmap with XML format works')

        rangestr = 'bytes=%s-%s' % (osize / 3, osize / 2)
        resp = pithos.object_get(obj, data_range=rangestr, success=(200, 206))
        partsize = int(resp.headers['content-length'])
        self.assertTrue(0 < partsize and partsize <= 1 + osize / 3)
        self.info('Range x-y works')
        orig = resp.text

        rangestr = 'bytes=%s' % (osize / 3)
        resp = pithos.object_get(
            obj, data_range=rangestr, if_range=True, success=(200, 206))
        partsize = int(resp.headers['content-length'])
        self.assertTrue(partsize, 1 + (osize / 3))
        diff = set(resp.text).symmetric_difference(set(orig[:partsize]))
        self.assertEqual(len(diff), 0)
        self.info('Range x works')

        rangestr = 'bytes=-%s' % (osize / 3)
        resp = pithos.object_get(
            obj, data_range=rangestr, if_range=True, success=(200, 206))
        partsize = int(resp.headers['content-length'])
        self.assertTrue(partsize, osize / 3)
        diff = set(resp.text).symmetric_difference(set(orig[-partsize:]))
        self.assertEqual(len(diff), 0)
        self.info('Range -x works')

        resp = pithos.object_get(obj, if_etag_match=etag)
        self.assertEqual(resp.status_code, 200)
        self.info('if-etag-match works')

        resp = pithos.object_get(obj, if_etag_not_match=etag + 'LALALA')
        self.assertEqual(resp.status_code, 200)
        self.info('if-etag-not-match works')

        for date_format in pithos.DATE_FORMATS:
            now_formated = self.now_unformated.strftime(date_format)
            resp1 = pithos.object_get(
                obj, if_modified_since=now_formated, success=(200, 304, 412))
            resp2 = pithos.object_get(
                obj, if_unmodified_since=now_formated, success=(200, 304, 412))
            self.assertNotEqual(resp1.status_code, resp2.status_code)
        self.info('if(un)modified-since works')

        obj, dnl_f = 'dir/sample.file', NamedTemporaryFile()
        self.info('Download %s as %s ...' % (obj, dnl_f.name))
        pithos.download_object(obj, dnl_f)
        self.info('Download is completed')

        f_size = len(orig)
        for pos in (0, f_size / 2, f_size - 128):
            dnl_f.seek(pos)
            self.large_file.seek(pos)
            self.assertEqual(self.large_file.read(64), dnl_f.read(64))
        self.info('Sampling shows that files match')

        # Upload a boring file
        self.info("Create a boring file of 42 blocks...")
        bor_f = self._create_boring_file(42)
        trg_fname = 'dir/uploaded.file'
        self.info('Now, upload the boring file as %s...' % trg_fname)
        pithos.upload_object(trg_fname, bor_f)
        self.info('Boring file %s is uploaded as %s' % (bor_f.name, trg_fname))
        dnl_f = NamedTemporaryFile()
        self.info('Download boring file as %s' % dnl_f.name)
        pithos.download_object(trg_fname, dnl_f)
        self.info('File is downloaded')

        for i in range(42):
            self.assertEqual(sample_block(bor_f, i), sample_block(dnl_f, i))

    def test_152_unique_containers(self):
        """Test if containers have unique names"""
        names = [n['name'] for n in self.containers]
        names = sorted(names)
        self.assertEqual(sorted(list(set(names))), names)

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
        pithos = cls.clients.pithos
        for tcont in getattr(cls, 'temp_containers', []):
            pithos.container = tcont
            try:
                pithos.del_container(delimiter='/')
                pithos.purge_container(tcont)
            except ClientError:
                pass
