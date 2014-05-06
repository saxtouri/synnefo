#!/usr/bin/env python
#
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

"""Unit Tests for the astakos-client module

Provides unit tests for the code implementing
the astakos client library

"""

import re
import sys
import simplejson

import astakosclient
from astakosclient import AstakosClient
from astakosclient.utils import join_urls
from astakosclient.errors import \
    AstakosClientException, Unauthorized, BadRequest, NotFound, \
    NoUserName, NoUUID, BadValue, QuotaLimit

# Use backported unittest functionality if Python < 2.7
try:
    import unittest2 as unittest
except ImportError:
    if sys.version_info < (2, 7):
        raise Exception("The unittest2 package is required for Python < 2.7")
    import unittest


# --------------------------------------------------------------------
# Helper functions
auth_url = "https://example.org/identity/v2.0"
account_prefix = "/account_prefix"
ui_prefix = "/ui_prefix"
oauth2_prefix = "/oauth2"
api_tokens = "/identity/v2.0/tokens"
api_usercatalogs = join_urls(account_prefix, "user_catalogs")
api_resources = join_urls(account_prefix, "resources")
api_quotas = join_urls(account_prefix, "quotas")
api_commissions = join_urls(account_prefix, "commissions")

# --------------------------------------
# Local users
token = {
    'id': "skzleaFlBl+fasFdaf24sx",
    'tenant': {
        'id': "73917abc-abcd-477e-a1f1-1763abcdefab",
        'name': "Example User One",
        },
    }

user = {
    'id': "73917abc-abcd-477e-a1f1-1763abcdefab",
    'name': "Example User One",
    'roles': [{u'id': 1, u'name': u'default'},
              {u'id': 5, u'name': u'academic-login-users'}],
    'roles_links': []
    }

resources = {
    "cyclades.ram": {
        "unit": "bytes",
        "description": "Virtual machine memory",
        "service": "cyclades"}}

endpoints = {
    "access": {
        "serviceCatalog": [
            {"endpoints": [{"SNF:uiURL": join_urls("https://example.org/",
                                                   ui_prefix),
                            "publicURL": join_urls("https://example.org/",
                                                   account_prefix),
                            "region": "default",
                            "versionId": "v1.0"}],
             "name": "astakos_account",
             "type": "account"
             },
            {"endpoints": [{"SNF:uiURL": join_urls("https://example.org/",
                                                   ui_prefix),
                            "publicURL": join_urls("https://example.org/",
                                                   oauth2_prefix),
                            "region": "default",
                            "versionId": "v1.0"}],
             "name": "astakos_oauth2",
             "type": "astakos_auth"
             }]
        }
    }

endpoints_with_info = dict(endpoints)
endpoints_with_info['access']['token'] = dict(token)
endpoints_with_info['access']['user'] = dict(user)

quotas = {
    "system": {
        "cyclades.ram": {
            "pending": 0,
            "limit": 1073741824,
            "usage": 536870912},
        "cyclades.vm": {
            "pending": 0,
            "limit": 2,
            "usage": 2}},
    "project:1": {
        "cyclades.ram": {
            "pending": 0,
            "limit": 2147483648,
            "usage": 2147483648},
        "cyclades.vm": {
            "pending": 1,
            "limit": 5,
            "usage": 2}}}

commission_request = {
    "force": False,
    "auto_accept": False,
    "name": "my commission",
    "provisions": [
        {
            "holder": "c02f315b-7d84-45bc-a383-552a3f97d2ad",
            "source": "system",
            "resource": "cyclades.vm",
            "quantity": 1
        },
        {
            "holder": "c02f315b-7d84-45bc-a383-552a3f97d2ad",
            "source": "system",
            "resource": "cyclades.ram",
            "quantity": 30000
        }]}

commission_successful_response = {"serial": 57}

commission_failure_response = {
    "overLimit": {
        "message": "a human-readable error message",
        "code": 413,
        "data": {
            "provision": {
                "holder": "c02f315b-7d84-45bc-a383-552a3f97d2ad",
                "source": "system",
                "resource": "cyclades.ram",
                "quantity": 520000000},
            "name": "NoCapacityError",
            "limit": 600000000,
            "usage": 180000000}}}

pending_commissions = [100, 200]

commission_description = {
    "serial": 57,
    "issue_time": "2013-04-08T10:19:15.0373+00:00",
    "name": "a commission",
    "provisions": [
        {
            "holder": "c02f315b-7d84-45bc-a383-552a3f97d2ad",
            "source": "system",
            "resource": "cyclades.vm",
            "quantity": 1
        },
        {
            "holder": "c02f315b-7d84-45bc-a383-552a3f97d2ad",
            "source": "system",
            "resource": "cyclades.ram",
            "quantity": 536870912
        }]}

resolve_commissions_req = {
    "accept": [56, 57],
    "reject": [56, 58, 59]}

resolve_commissions_rep = {
    "accepted": [57],
    "rejected": [59],
    "failed": [
        [56, {
            "badRequest": {
                "message": "cannot both accept and reject serial 56",
                "code": 400}}],
        [58, {
            "itemNotFound": {
                "message": "serial 58 does not exist",
                "code": 404}}]]}


# ----------------------------
# These functions will be used as mocked requests
def _request_status_302(conn, method, url, **kwargs):
    """This request returns 302"""
    message = "FOUND"
    status = 302
    data = "302 Found"
    return (message, data, status)


def _request_status_404(conn, method, url, **kwargs):
    """This request returns 404"""
    message = "Not Found"
    status = 404
    data = "404 Not Found"
    return (message, data, status)


def _request_status_403(conn, method, url, **kwargs):
    """This request returns 403"""
    message = "UNAUTHORIZED"
    status = 403
    data = "Forbidden"
    return (message, data, status)


def _request_status_401(conn, method, url, **kwargs):
    """This request returns 401"""
    message = "UNAUTHORIZED"
    status = 401
    data = "Invalid X-Auth-Token\n"
    return (message, data, status)


def _request_status_400(conn, method, url, **kwargs):
    """This request returns 400"""
    message = "BAD REQUEST"
    status = 400
    data = "Method not allowed.\n"
    return (message, data, status)


def _mock_request(conn, method, url, **kwargs):
    """This request behaves like original Astakos does"""
    if api_tokens == url:
        return _req_tokens(conn, method, url, **kwargs)
    elif api_usercatalogs == url:
        return _req_catalogs(conn, method, url, **kwargs)
    elif api_resources == url:
        return _req_resources(conn, method, url, **kwargs)
    elif api_quotas == url:
        return _req_quotas(conn, method, url, **kwargs)
    elif url.startswith(api_commissions):
        return _req_commission(conn, method, url, **kwargs)
    else:
        return _request_status_404(conn, method, url, **kwargs)


def _req_tokens(conn, method, url, **kwargs):
    """Return endpoints"""
    global token, user, endpoints

    # Check input
    if conn.__class__.__name__ != "HTTPSConnection":
        return _request_status_302(conn, method, url, **kwargs)
    if method != "POST":
        return _request_status_400(conn, method, url, **kwargs)
    req_token = kwargs['headers'].get('X-Auth-Token')
    if req_token != token['id']:
        return _request_status_401(conn, method, url, **kwargs)

    if 'body' in kwargs:
        # Return endpoints with authenticate info
        return ("", simplejson.dumps(endpoints_with_info), 200)
    else:
        # Return endpoints without authenticate info
        return ("", simplejson.dumps(endpoints), 200)


def _req_catalogs(conn, method, url, **kwargs):
    """Return user catalogs"""
    global token, user

    # Check input
    if conn.__class__.__name__ != "HTTPSConnection":
        return _request_status_302(conn, method, url, **kwargs)
    if method != "POST":
        return _request_status_400(conn, method, url, **kwargs)
    req_token = kwargs['headers'].get('X-Auth-Token')
    if req_token != token['id']:
        return _request_status_401(conn, method, url, **kwargs)

    # Return
    body = simplejson.loads(kwargs['body'])
    if 'uuids' in body:
        # Return uuid_catalog
        uuids = body['uuids']
        catalogs = {}
        if user['id'] in uuids:
            catalogs[user['id']] = user['name']
        return_catalog = {"displayname_catalog": {}, "uuid_catalog": catalogs}
    elif 'displaynames' in body:
        # Return displayname_catalog
        names = body['displaynames']
        catalogs = {}
        if user['name'] in names:
            catalogs[user['name']] = user['id']
        return_catalog = {"displayname_catalog": catalogs, "uuid_catalog": {}}
    else:
        return_catalog = {"displayname_catalog": {}, "uuid_catalog": {}}
    return ("", simplejson.dumps(return_catalog), 200)


def _req_resources(conn, method, url, **kwargs):
    """Return quota resources"""
    global resources

    # Check input
    if conn.__class__.__name__ != "HTTPSConnection":
        return _request_status_302(conn, method, url, **kwargs)
    if method != "GET":
        return _request_status_400(conn, method, url, **kwargs)

    # Return
    return ("", simplejson.dumps(resources), 200)


def _req_quotas(conn, method, url, **kwargs):
    """Return quotas for user_1"""
    global token, quotas

    # Check input
    if conn.__class__.__name__ != "HTTPSConnection":
        return _request_status_302(conn, method, url, **kwargs)
    if method != "GET":
        return _request_status_400(conn, method, url, **kwargs)
    req_token = kwargs['headers'].get('X-Auth-Token')
    if req_token != token['id']:
        return _request_status_401(conn, method, url, **kwargs)

    # Return
    return ("", simplejson.dumps(quotas), 200)


def _req_commission(conn, method, url, **kwargs):
    """Perform a commission for user_1"""
    global token, pending_commissions, \
        commission_successful_response, commission_failure_response

    # Check input
    if conn.__class__.__name__ != "HTTPSConnection":
        return _request_status_302(conn, method, url, **kwargs)
    req_token = kwargs['headers'].get('X-Auth-Token')
    if req_token != token['id']:
        return _request_status_401(conn, method, url, **kwargs)

    if method == "POST":
        if 'body' not in kwargs:
            return _request_status_400(conn, method, url, **kwargs)
        body = simplejson.loads(unicode(kwargs['body']))
        if re.match('/?'+api_commissions+'$', url) is not None:
            # Issue Commission
            # Check if we have enough resources to give
            if body['provisions'][1]['quantity'] > 420000000:
                return ("", simplejson.dumps(commission_failure_response), 413)
            else:
                return \
                    ("", simplejson.dumps(commission_successful_response), 200)
        else:
            # Issue commission action
            serial = url.split('/')[3]
            if serial == "action":
                # Resolve multiple actions
                if body == resolve_commissions_req:
                    return ("", simplejson.dumps(resolve_commissions_rep), 200)
                else:
                    return _request_status_400(conn, method, url, **kwargs)
            else:
                # Issue action for one commission
                if serial != str(57):
                    return _request_status_404(conn, method, url, **kwargs)
                if len(body) != 1:
                    return _request_status_400(conn, method, url, **kwargs)
                if "accept" not in body.keys() and "reject" not in body.keys():
                    return _request_status_400(conn, method, url, **kwargs)
                return ("", "", 200)

    elif method == "GET":
        if re.match('/?'+api_commissions+'$', url) is not None:
            # Return pending commission
            return ("", simplejson.dumps(pending_commissions), 200)
        else:
            # Return commissions's description
            serial = re.sub('/?' + api_commissions, '', url)[1:]
            if serial == str(57):
                return ("", simplejson.dumps(commission_description), 200)
            else:
                return _request_status_404(conn, method, url, **kwargs)
    else:
        return _request_status_400(conn, method, url, **kwargs)


# --------------------------------------------------------------------
# The actual tests

class TestCallAstakos(unittest.TestCase):
    """Test cases for function _callAstakos"""

    # Patch astakosclient's _do_request function
    def setUp(self):  # noqa
        astakosclient._do_request = _mock_request

    # ----------------------------------
    # Test the response we get if we send invalid token
    def _invalid_token(self, pool):
        global auth_url
        token = "skaksaFlBl+fasFdaf24sx"
        try:
            client = AstakosClient(token, auth_url, use_pool=pool)
            client.authenticate()
        except Unauthorized:
            pass
        except Exception:
            self.fail("Should have returned 401 (Invalid X-Auth-Token)")
        else:
            self.fail("Should have returned 401 (Invalid X-Auth-Token)")

    def test_invalid_token(self):
        """Test _invalid_token without pool"""
        self._invalid_token(False)

    def test_invalid_token_pool(self):
        """Test _invalid_token using pool"""
        self._invalid_token(True)

    # ----------------------------------
    # Test the response we get if we send invalid url
    def _invalid_url(self, pool):
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url, use_pool=pool)
            client._call_astakos("/astakos/api/misspelled")
        except NotFound:
            pass
        except Exception, e:
            self.fail("Got \"%s\" instead of 404" % e)
        else:
            self.fail("Should have returned 404 (Not Found)")

    def test_invalid_url(self):
        """Test _invalid_url without pool"""
        self._invalid_url(False)

    def test_invalid_url_pool(self):
        """Test _invalid_url using pool"""
        self._invalid_url(True)

    # ----------------------------------
    # Test the response we get if we use an unsupported scheme
    def _unsupported_scheme(self, pool):
        global token, auth_url
        try:
            client = AstakosClient(
                token['id'], "ftp://example.com", use_pool=pool)
            client.authenticate()
        except BadValue:
            pass
        except Exception:
            self.fail("Should have raise BadValue Exception")
        else:
            self.fail("Should have raise BadValue Exception")

    def test_unsupported_scheme(self):
        """Test _unsupported_scheme without pool"""
        self._unsupported_scheme(False)

    def test_unsupported_scheme_pool(self):
        """Test _unsupported_scheme using pool"""
        self._unsupported_scheme(True)

    # ----------------------------------
    # Test the response we get if we use http instead of https
    def _http_scheme(self, pool):
        global token
        http_auth_url = "http://example.org/identity/v2.0"
        try:
            client = AstakosClient(token['id'], http_auth_url, use_pool=pool)
            client.authenticate()
        except AstakosClientException as err:
            if err.status != 302:
                self.fail("Should have returned 302 (Found)")
        else:
            self.fail("Should have returned 302 (Found)")

    def test_http_scheme(self):
        """Test _http_scheme without pool"""
        self._http_scheme(False)

    def test_http_scheme_pool(self):
        """Test _http_scheme using pool"""
        self._http_scheme(True)

    # ----------------------------------
    # Test the response we get if we use authenticate with GET
    def _get_authenticate(self, pool):
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url, use_pool=pool)
            client._call_astakos(api_tokens, method="GET")
        except BadRequest:
            pass
        except Exception:
            self.fail("Should have returned 400 (Method not allowed)")
        else:
            self.fail("Should have returned 400 (Method not allowed)")

    def test_get_authenticate(self):
        """Test _get_authenticate without pool"""
        self._get_authenticate(False)

    def test_get_authenticate_pool(self):
        """Test _get_authenticate using pool"""
        self._get_authenticate(True)

    # ----------------------------------
    # Test the response if we request user_catalogs with GET
    def _get_user_catalogs(self, pool):
        global token, auth_url, api_usercatalogs
        try:
            client = AstakosClient(token['id'], auth_url, use_pool=pool)
            client._call_astakos(api_usercatalogs)
        except BadRequest:
            pass
        except Exception:
            self.fail("Should have returned 400 (Method not allowed)")
        else:
            self.fail("Should have returned 400 (Method not allowed)")

    def test_get_user_catalogs(self):
        """Test _get_user_catalogs without pool"""
        self._get_user_catalogs(False)

    def test_get_user_catalogs_pool(self):
        """Test _get_user_catalogs using pool"""
        self._get_user_catalogs(True)


class TestAuthenticate(unittest.TestCase):
    """Test cases for function getUserInfo"""

    # Patch astakosclient's _do_request function
    def setUp(self):  # noqa
        astakosclient._do_request = _mock_request

    # ----------------------------------
    # Test the response we get for invalid token
    def _invalid_token(self, pool):
        global auth_url
        token = "skaksaFlBl+fasFdaf24sx"
        try:
            client = AstakosClient(token, auth_url, use_pool=pool)
            client.authenticate()
        except Unauthorized:
            pass
        except Exception:
            self.fail("Should have returned 401 (Invalid X-Auth-Token)")
        else:
            self.fail("Should have returned 401 (Invalid X-Auth-Token)")

    def test_invalid_token(self):
        """Test _invalid_token without pool"""
        self._invalid_token(False)

    def test_invalid_token_pool(self):
        """Test _invalid_token using pool"""
        self._invalid_token(True)

    # ----------------------------------
    # Test response for user
    def _auth_user(self, pool):
        global token, endpoints_with_info, auth_url
        try:
            client = AstakosClient(token['id'], auth_url, use_pool=pool)
            auth_info = client.authenticate()
        except Exception as err:
            self.fail("Shouldn't raise an Exception: %s" % err)
        self.assertEqual(endpoints_with_info, auth_info)

    def test_auth_user(self):
        """Test _auth_user without pool"""
        self._auth_user(False)

    def test_auth_user_pool(self):
        """Test _auth_user for User 1 using pool, with usage"""
        self._auth_user(True)


class TestDisplayNames(unittest.TestCase):
    """Test cases for functions getDisplayNames/getDisplayName"""

    # Patch astakosclient's _do_request function
    def setUp(self):  # noqa
        astakosclient._do_request = _mock_request

    # ----------------------------------
    # Test the response we get for invalid token
    def test_invalid_token(self):
        """Test the response we get for invalid token (without pool)"""
        global auth_url
        token = "skaksaFlBl+fasFdaf24sx"
        try:
            client = AstakosClient(token, auth_url)
            client.get_usernames(["12412351"])
        except Unauthorized:
            pass
        except Exception:
            self.fail("Should have returned 401 (Invalid X-Auth-Token)")
        else:
            self.fail("Should have returned 401 (Invalid X-Auth-Token)")

    # ----------------------------------
    # Get username
    def test_username(self):
        """Test get_username"""
        global token, user, auth_url
        try:
            client = AstakosClient(token['id'], auth_url,
                                   use_pool=False, retry=2)
            info = client.get_username(user['id'])
        except Exception, e:
            self.fail("Shouldn't raise an Exception: %s" % e)
        self.assertEqual(info, user['name'])

    # ----------------------------------
    # Get info with wrong uuid
    def test_no_username(self):
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            client.get_username("1234")
        except NoUserName:
            pass
        except:
            self.fail("Should have raised NoDisplayName exception")
        else:
            self.fail("Should have raised NoDisplayName exception")


class TestGetUUIDs(unittest.TestCase):
    """Test cases for functions getUUIDs/getUUID"""

    # Patch astakosclient's _do_request function
    def setUp(self):  # noqa
        astakosclient._do_request = _mock_request

    # ----------------------------------
    # Test the response we get for invalid token
    def test_invalid_token(self):
        """Test the response we get for invalid token (using pool)"""
        global user, auth_url
        token = "skaksaFlBl+fasFdaf24sx"
        try:
            client = AstakosClient(token, auth_url)
            client.get_uuids([user['name']])
        except Unauthorized:
            pass
        except Exception:
            self.fail("Should have returned 401 (Invalid X-Auth-Token)")
        else:
            self.fail("Should have returned 401 (Invalid X-Auth-Token)")

    # ----------------------------------
    # Get uuid
    def test_get_uuid(self):
        """Test get_uuid"""
        global token, user, auth_url
        try:
            client = AstakosClient(token['id'], auth_url, retry=1)
            catalog = client.get_uuids([user['name']])
        except:
            self.fail("Shouldn't raise an Exception")
        self.assertEqual(catalog[user['name']], user['id'])

    # ----------------------------------
    # Get uuid with wrong username
    def test_no_uuid(self):
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            client.get_uuid("1234")
        except NoUUID:
            pass
        except:
            self.fail("Should have raised NoUUID exception")
        else:
            self.fail("Should have raised NoUUID exception")


class TestResources(unittest.TestCase):
    """Test cases for function get_resources"""

    # Patch astakosclient's _do_request function
    def setUp(self):  # noqa
        astakosclient._do_request = _mock_request

    # ----------------------------------
    def test_get_resources(self):
        """Test function call of get_resources"""
        global resources, auth_url, token
        try:
            client = AstakosClient(token['id'], auth_url, retry=1)
            result = client.get_resources()
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        self.assertEqual(resources, result)


class TestQuotas(unittest.TestCase):
    """Test cases for function get_quotas"""

    # Patch astakosclient's _do_request function
    def setUp(self):  # noqa
        astakosclient._do_request = _mock_request

    # ----------------------------------
    def test_get_quotas(self):
        """Test function call of get_quotas"""
        global quotas, token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            result = client.get_quotas()
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        self.assertEqual(quotas, result)

    # -----------------------------------
    def test_get_quotas_unauthorized(self):
        """Test function call of get_quotas with wrong token"""
        global auth_url
        token = "buahfhsda"
        try:
            client = AstakosClient(token, auth_url)
            client.get_quotas()
        except Unauthorized:
            pass
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        else:
            self.fail("Should have raised Unauthorized Exception")


class TestCommissions(unittest.TestCase):
    """Test cases for quota commissions"""

    # Patch astakosclient's _do_request function
    def setUp(self):  # noqa
        astakosclient._do_request = _mock_request

    # ----------------------------------
    def test_issue_commission(self):
        """Test function call of issue_commission"""
        global token, commission_request, commission_successful_reqsponse
        global auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            response = client._issue_commission(commission_request)
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        self.assertEqual(response, commission_successful_response['serial'])

    # ----------------------------------
    def test_issue_commission_quota_limit(self):
        """Test function call of issue_commission with limit exceeded"""
        global token, commission_request, commission_failure_response
        global auth_url
        new_request = dict(commission_request)
        new_request['provisions'][1]['quantity'] = 520000000
        try:
            client = AstakosClient(token['id'], auth_url)
            client._issue_commission(new_request)
        except QuotaLimit:
            pass
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        else:
            self.fail("Should have raised QuotaLimit Exception")

    # ----------------------------------
    def test_issue_one_commission(self):
        """Test function call of issue_one_commission"""
        global token, commission_successful_response, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            response = client.issue_one_commission(
                "c02f315b-7d84-45bc-a383-552a3f97d2ad",
                {("system", "cyclades.vm"): 1,
                 ("system", "cyclades.ram"): 30000})
        except Exception as err:
            self.fail("Shouldn't have raised Exception %s" % err)
        self.assertEqual(response, commission_successful_response['serial'])

    # ----------------------------------
    def test_get_pending_commissions(self):
        """Test function call of get_pending_commissions"""
        global token, pending_commissions, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            response = client.get_pending_commissions()
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        self.assertEqual(response, pending_commissions)

    # ----------------------------------
    def test_get_commission_info(self):
        """Test function call of get_commission_info"""
        global token, commission_description, auth_url
        try:
            client = AstakosClient(token['id'], auth_url,
                                   use_pool=True, pool_size=2)
            response = client.get_commission_info(57)
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        self.assertEqual(response, commission_description)

    # ----------------------------------
    def test_get_commission_info_not_found(self):
        """Test function call of get_commission_info with invalid serial"""
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            client.get_commission_info("57lala")
        except NotFound:
            pass
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        else:
            self.fail("Should have raised NotFound")

    # ----------------------------------
    def test_get_commission_info_without_serial(self):
        """Test function call of get_commission_info without serial"""
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            client.get_commission_info(None)
        except BadValue:
            pass
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        else:
            self.fail("Should have raise BadValue")

    # ----------------------------------
    def test_commision_action(self):
        """Test function call of commision_action with wrong action"""
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            client.commission_action(57, "lala")
        except BadRequest:
            pass
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        else:
            self.fail("Should have raised BadRequest")

    # ----------------------------------
    def test_accept_commission(self):
        """Test function call of accept_commission"""
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            client.accept_commission(57)
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)

    # ----------------------------------
    def test_reject_commission(self):
        """Test function call of reject_commission"""
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            client.reject_commission(57)
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)

    # ----------------------------------
    def test_accept_commission_not_found(self):
        """Test function call of accept_commission with wrong serial"""
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            client.reject_commission(20)
        except NotFound:
            pass
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        else:
            self.fail("Should have raised NotFound")

    # ----------------------------------
    def test_resolve_commissions(self):
        """Test function call of resolve_commissions"""
        global token, auth_url
        try:
            client = AstakosClient(token['id'], auth_url)
            result = client.resolve_commissions([56, 57], [56, 58, 59])
        except Exception as err:
            self.fail("Shouldn't raise Exception %s" % err)
        self.assertEqual(result, resolve_commissions_rep)


# ----------------------------
# Run tests
if __name__ == "__main__":
    unittest.main()
