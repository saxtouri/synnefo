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

import sys
import uuid as uuidlib
import logging
import hashlib
import binascii

from collections import defaultdict
from functools import wraps, partial
from traceback import format_exc
from time import time

from pithos.workers import glue
from archipelago.common import Segment, Xseg_ctx
from objpool import ObjectPool


try:
    from astakosclient import AstakosClient
except ImportError:
    AstakosClient = None

from pithos.backends.base import (
    DEFAULT_ACCOUNT_QUOTA, DEFAULT_CONTAINER_QUOTA,
    DEFAULT_CONTAINER_VERSIONING, NotAllowedError, QuotaError,
    BaseBackend, AccountExists, ContainerExists, AccountNotEmpty,
    ContainerNotEmpty, ItemNotExists, VersionNotExists,
    InvalidHash, IllegalOperationError)


class DisabledAstakosClient(object):
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __getattr__(self, name):
        m = ("AstakosClient has been disabled, "
             "yet an attempt to access it was made")
        raise AssertionError(m)


# Stripped-down version of the HashMap class found in tools.

class HashMap(list):

    def __init__(self, blocksize, blockhash):
        super(HashMap, self).__init__()
        self.blocksize = blocksize
        self.blockhash = blockhash

    def _hash_raw(self, v):
        h = hashlib.new(self.blockhash)
        h.update(v)
        return h.digest()

    def hash(self):
        if len(self) == 0:
            return self._hash_raw('')
        if len(self) == 1:
            return self.__getitem__(0)

        h = list(self)
        s = 2
        while s < len(h):
            s = s * 2
        h += [('\x00' * len(h[0]))] * (s - len(h))
        while len(h) > 1:
            h = [self._hash_raw(h[x] + h[x + 1]) for x in range(0, len(h), 2)]
        return h[0]

# Default modules and settings.
DEFAULT_DB_MODULE = 'pithos.backends.lib.sqlalchemy'
DEFAULT_DB_CONNECTION = 'sqlite:///backend.db'
DEFAULT_BLOCK_MODULE = 'pithos.backends.lib.hashfiler'
DEFAULT_BLOCK_PATH = 'data/'
DEFAULT_BLOCK_UMASK = 0o022
DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024  # 4MB
DEFAULT_HASH_ALGORITHM = 'sha256'
#DEFAULT_QUEUE_MODULE = 'pithos.backends.lib.rabbitmq'
DEFAULT_BLOCK_PARAMS = {'mappool': None, 'blockpool': None}
#DEFAULT_QUEUE_HOSTS = '[amqp://guest:guest@localhost:5672]'
#DEFAULT_QUEUE_EXCHANGE = 'pithos'
DEFAULT_PUBLIC_URL_ALPHABET = ('0123456789'
                               'abcdefghijklmnopqrstuvwxyz'
                               'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
DEFAULT_PUBLIC_URL_SECURITY = 16
DEFAULT_ARCHIPELAGO_CONF_FILE = '/etc/archipelago/archipelago.conf'

QUEUE_MESSAGE_KEY_PREFIX = 'pithos.%s'
QUEUE_CLIENT_ID = 'pithos'
QUEUE_INSTANCE_ID = '1'

(CLUSTER_NORMAL, CLUSTER_HISTORY, CLUSTER_DELETED) = range(3)

QUOTA_POLICY = 'quota'
VERSIONING_POLICY = 'versioning'
PROJECT = 'project'

inf = float('inf')

ULTIMATE_ANSWER = 42

DEFAULT_DISKSPACE_RESOURCE = 'pithos.diskspace'

DEFAULT_MAP_CHECK_INTERVAL = 5  # set to 5 secs

logger = logging.getLogger(__name__)


def backend_method(func):
    @wraps(func)
    def wrapper(self, *args, **kw):
        # if we are inside a database transaction
        # just proceed with the method execution
        # otherwise manage a new transaction
        if self.in_transaction:
            return func(self, *args, **kw)

        try:
            self.pre_exec()
            result = func(self, *args, **kw)
            success_status = True
            return result
        except:
            success_status = False
            raise
        finally:
            self.post_exec(success_status)
    return wrapper


def debug_method(func):
    @wraps(func)
    def wrapper(self, *args, **kw):
        try:
            result = func(self, *args, **kw)
            return result
        except:
            result = format_exc()
            raise
        finally:
            all_args = map(repr, args)
            map(all_args.append, ('%s=%s' % (k, v) for k, v in kw.iteritems()))
            logger.debug(">>> %s(%s) <<< %s" % (
                func.__name__, ', '.join(all_args).rstrip(', '), result))
    return wrapper


def check_allowed_paths(action):
    """Decorator for backend methods checking path access granted to user.

    The 1st argument of the decorated method is expected to be a
    ModularBackend instance, the 2nd the user performing the request and
    the path join of the rest arguments is supposed to be the requested path.

    The decorator checks whether the requested path is among the user's allowed
    cached paths.
    If this is the case, the decorator returns immediately to reduce the
    interactions with the database.
    Otherwise, it proceeds with the execution of the decorated method and if
    the method returns successfully (no exceptions are raised), the requested
    path is added to the user's cached allowed paths.

    :param action: (int) 0 for reads / 1 for writes
    :raises NotAllowedError: the user does not have access to the path
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args):
            user = args[0]
            if action == self.READ:
                d = self.read_allowed_paths
            else:
                d = self.write_allowed_paths
            path = '/'.join(args[1:])
            if path in d.get(user, []):
                return  # access is already checked
            else:
                func(self, *args)   # proceed with access check
                d[user].add(path)  # add path in the allowed user paths
        return wrapper
    return decorator


def list_method(func):
    @wraps(func)
    def wrapper(self, *args, **kw):
        marker = kw.get('marker')
        limit = kw.get('limit')
        result = func(self, *args, **kw)
        start, limit = self._list_limits(result, marker, limit)
        return result[start:start + limit]
    return wrapper


class ModularBackend(BaseBackend):
    """A modular backend.

    Uses modules for SQL functions and storage.
    """

    def __init__(self, db_module=None, db_connection=None,
                 block_module=None, block_path=None, block_umask=None,
                 block_size=None, hash_algorithm=None,
                 queue_module=None, queue_hosts=None, queue_exchange=None,
                 astakos_auth_url=None, service_token=None,
                 astakosclient_poolsize=None,
                 free_versioning=True, block_params=None,
                 public_url_security=None,
                 public_url_alphabet=None,
                 account_quota_policy=None,
                 container_quota_policy=None,
                 container_versioning_policy=None,
                 archipelago_conf_file=None,
                 xseg_pool_size=8,
                 map_check_interval=None):
        db_module = db_module or DEFAULT_DB_MODULE
        db_connection = db_connection or DEFAULT_DB_CONNECTION
        block_module = block_module or DEFAULT_BLOCK_MODULE
        block_path = block_path or DEFAULT_BLOCK_PATH
        block_umask = block_umask or DEFAULT_BLOCK_UMASK
        block_params = block_params or DEFAULT_BLOCK_PARAMS
        block_size = block_size or DEFAULT_BLOCK_SIZE
        hash_algorithm = hash_algorithm or DEFAULT_HASH_ALGORITHM
        #queue_module = queue_module or DEFAULT_QUEUE_MODULE
        account_quota_policy = account_quota_policy or DEFAULT_ACCOUNT_QUOTA
        container_quota_policy = container_quota_policy \
            or DEFAULT_CONTAINER_QUOTA
        container_versioning_policy = container_versioning_policy \
            or DEFAULT_CONTAINER_VERSIONING
        archipelago_conf_file = archipelago_conf_file \
            or DEFAULT_ARCHIPELAGO_CONF_FILE
        map_check_interval = map_check_interval \
            or DEFAULT_MAP_CHECK_INTERVAL

        self.default_account_policy = {}
        self.default_container_policy = {
            QUOTA_POLICY: container_quota_policy,
            VERSIONING_POLICY: container_versioning_policy,
            PROJECT: None
        }
        #queue_hosts = queue_hosts or DEFAULT_QUEUE_HOSTS
        #queue_exchange = queue_exchange or DEFAULT_QUEUE_EXCHANGE

        self.public_url_security = (public_url_security or
                                    DEFAULT_PUBLIC_URL_SECURITY)
        self.public_url_alphabet = (public_url_alphabet or
                                    DEFAULT_PUBLIC_URL_ALPHABET)

        self.hash_algorithm = hash_algorithm
        self.block_size = block_size
        self.free_versioning = free_versioning
        self.map_check_interval = map_check_interval

        def load_module(m):
            __import__(m)
            return sys.modules[m]

        self.db_module = load_module(db_module)
        self.wrapper = self.db_module.DBWrapper(db_connection)
        params = {'wrapper': self.wrapper}
        self.permissions = self.db_module.Permissions(**params)
        self.config = self.db_module.Config(**params)
        self.commission_serials = self.db_module.QuotaholderSerial(**params)
        for x in ['READ', 'WRITE']:
            setattr(self, x, getattr(self.db_module, x))
        self.node = self.db_module.Node(**params)
        for x in ['ROOTNODE', 'SERIAL', 'NODE', 'HASH', 'SIZE', 'TYPE',
                  'MTIME', 'MUSER', 'UUID', 'CHECKSUM', 'CLUSTER',
                  'MATCH_PREFIX', 'MATCH_EXACT',
                  'AVAILABLE', 'MAP_CHECK_TIMESTAMP']:
            setattr(self, x, getattr(self.db_module, x))

        self.ALLOWED = ['read', 'write']

        glue.WorkerGlue.setupXsegPool(ObjectPool, Segment, Xseg_ctx,
                                      cfile=archipelago_conf_file,
                                      pool_size=xseg_pool_size)
        self.block_module = load_module(block_module)
        self.block_params = block_params
        params = {'path': block_path,
                  'block_size': self.block_size,
                  'hash_algorithm': self.hash_algorithm,
                  'umask': block_umask}
        params.update(self.block_params)
        self.store = self.block_module.Store(**params)

        if queue_module and queue_hosts:
            self.queue_module = load_module(queue_module)
            params = {'hosts': queue_hosts,
                      'exchange': queue_exchange,
                      'client_id': QUEUE_CLIENT_ID}
            self.queue = self.queue_module.Queue(**params)
        else:
            class NoQueue:
                def send(self, *args):
                    pass

                def close(self):
                    pass

            self.queue = NoQueue()

        self.astakos_auth_url = astakos_auth_url
        self.service_token = service_token

        if not astakos_auth_url or not AstakosClient:
            self.astakosclient = DisabledAstakosClient(
                service_token, astakos_auth_url,
                use_pool=True,
                pool_size=astakosclient_poolsize)
        else:
            self.astakosclient = AstakosClient(
                service_token, astakos_auth_url,
                use_pool=True,
                pool_size=astakosclient_poolsize)

        self.serials = []
        self.messages = []

        self._move_object = partial(self._copy_object, is_move=True)

        self.lock_container_path = False

        self.in_transaction = False

        self._reset_allowed_paths()

    def pre_exec(self, lock_container_path=False):
        self.lock_container_path = lock_container_path
        self.wrapper.execute()
        self.serials = []
        self._reset_allowed_paths()
        self.in_transaction = True

    def post_exec(self, success_status=True):
        if success_status:
            # send messages produced
            for m in self.messages:
                self.queue.send(*m)

            # register serials
            if self.serials:
                self.commission_serials.insert_many(
                    self.serials)

                # commit to ensure that the serials are registered
                # even if resolve commission fails
                self.wrapper.commit()

                # start new transaction
                self.wrapper.execute()

                r = self.astakosclient.resolve_commissions(
                    accept_serials=self.serials,
                    reject_serials=[])
                self.commission_serials.delete_many(
                    r['accepted'])

            self.wrapper.commit()
        else:
            if self.serials:
                r = self.astakosclient.resolve_commissions(
                    accept_serials=[],
                    reject_serials=self.serials)
                self.commission_serials.delete_many(
                    r['rejected'])
            self.wrapper.rollback()
        self.in_transaction = False

    def close(self):
        self.wrapper.close()
        self.queue.close()

    @property
    def using_external_quotaholder(self):
        return not isinstance(self.astakosclient, DisabledAstakosClient)

    @debug_method
    @backend_method
    @list_method
    def list_accounts(self, user, marker=None, limit=10000):
        """Return a list of accounts the user can access."""

        return self._allowed_accounts(user)

    @debug_method
    @backend_method
    def get_account_meta(self, user, account, domain=None, until=None,
                         include_user_defined=True):
        """Return a dictionary with the account metadata for the domain."""

        self._can_read_account(user, account)
        path, node = self._lookup_account(account, user == account)
        if user != account:
            if until or (node is None):
                raise NotAllowedError
        try:
            props = self._get_properties(node, until)
            mtime = props[self.MTIME]
        except NameError:
            props = None
            mtime = until
        count, bytes, tstamp = self._get_statistics(node, until, compute=True)
        tstamp = max(tstamp, mtime)
        if until is None:
            modified = tstamp
        else:
            modified = self._get_statistics(
                node, compute=True)[2]  # Overall last modification.
            modified = max(modified, mtime)

        if user != account:
            meta = {'name': account}
        else:
            meta = {}
            if props is not None and include_user_defined:
                if domain is None:
                    raise ValueError(
                        'Domain argument is obligatory for getting '
                        'user defined metadata')
                meta.update(
                    dict(self.node.attribute_get(props[self.SERIAL], domain)))
            if until is not None:
                meta.update({'until_timestamp': tstamp})
            meta.update({'name': account, 'count': count, 'bytes': bytes})
            if self.using_external_quotaholder:
                external_quota = self.astakosclient.service_get_quotas(
                    account)[account]
                meta['bytes'] = sum(d['pithos.diskspace']['usage'] for d in
                                    external_quota.values())
        meta.update({'modified': modified})
        return meta

    @debug_method
    @backend_method
    def update_account_meta(self, user, account, domain, meta, replace=False):
        """Update the metadata associated with the account for the domain."""

        self._can_write_account(user, account)
        path, node = self._lookup_account(account, True)
        self._put_metadata(user, node, domain, meta, replace,
                           update_statistics_ancestors_depth=-1)

    @debug_method
    @backend_method
    def get_account_groups(self, user, account):
        """Return a dictionary with the user groups defined for the account."""

        self._can_read_account(user, account)
        if user != account:
            return {}
        self._lookup_account(account, True)
        return self.permissions.group_dict(account)

    @debug_method
    @backend_method
    def update_account_groups(self, user, account, groups, replace=False):
        """Update the groups associated with the account."""

        self._can_write_account(user, account)
        self._lookup_account(account, True)
        self._check_groups(groups)
        if replace:
            self.permissions.group_destroy(account)
        for k, v in groups.iteritems():
            if not replace:  # If not already deleted.
                self.permissions.group_delete(account, k)
            if v:
                self.permissions.group_addmany(account, k, v)

    @debug_method
    @backend_method
    def get_account_policy(self, user, account):
        """Return a dictionary with the account policy."""

        self._can_read_account(user, account)
        if user != account:
            return {}
        path, node = self._lookup_account(account, True)
        policy = self._get_policy(node, is_account_policy=True)
        if self.using_external_quotaholder:
            external_quota = self.astakosclient.service_get_quotas(
                account)[account]
            policy.update(dict(('%s-%s' % (QUOTA_POLICY, k),
                                v['pithos.diskspace']['limit']) for k, v in
                               external_quota.items()))

        return policy

    @debug_method
    @backend_method
    def update_account_policy(self, user, account, policy, replace=False):
        """Update the policy associated with the account."""

        self._can_write_account(user, account)
        path, node = self._lookup_account(account, True)
        self._put_policy(node, policy, replace, is_account_policy=True,
                         check=True)

    @debug_method
    @backend_method
    def put_account(self, user, account, policy=None):
        """Create a new account with the given name."""

        policy = policy or {}
        self._can_write_account(user, account)
        node = self.node.node_lookup(account)
        if node is not None:
            raise AccountExists('Account already exists')
        node = self._put_path(user, self.ROOTNODE, account,
                              update_statistics_ancestors_depth=-1)
        self._put_policy(node, policy, True, is_account_policy=True,
                         check=True if policy else False)

    @debug_method
    @backend_method
    def delete_account(self, user, account):
        """Delete the account with the given name."""

        self._can_write_account(user, account)
        node = self.node.node_lookup(account)
        if node is None:
            return
        if not self.node.node_remove(node,
                                     update_statistics_ancestors_depth=-1):
            raise AccountNotEmpty('Account is not empty')
        self.permissions.group_destroy(account)

        # remove all the cached allowed paths
        # removing the specific path could be more expensive
        self._reset_allowed_paths()

    @debug_method
    @backend_method
    @list_method
    def list_containers(self, user, account, marker=None, limit=10000,
                        shared=False, until=None, public=False):
        """Return a list of containers existing under an account."""

        self._can_read_account(user, account)
        if user != account:
            if until:
                raise NotAllowedError
            return self._allowed_containers(user, account)
        if shared or public:
            allowed = set()
            if shared:
                allowed.update([x.split('/', 2)[1] for x in
                               self.permissions.access_list_shared(account)])
            if public:
                allowed.update([x[0].split('/', 2)[1] for x in
                               self.permissions.public_list(account)])
            return sorted(allowed)
        node = self.node.node_lookup(account)
        return [x[0] for x in self._list_object_properties(
            node, account, '', '/', marker, limit, False, None, [], until)]

    @debug_method
    @backend_method
    def list_container_meta(self, user, account, container, domain,
                            until=None):
        """Return a list of the container's object meta keys for a domain."""

        self._can_read_container(user, account, container)
        allowed = []
        if user != account:
            if until:
                raise NotAllowedError
        path, node = self._lookup_container(account, container)
        before = until if until is not None else inf
        allowed = self._get_formatted_paths(allowed)
        return self.node.latest_attribute_keys(node, domain, before,
                                               CLUSTER_DELETED, allowed)

    @debug_method
    @backend_method
    def get_container_meta(self, user, account, container, domain=None,
                           until=None, include_user_defined=True):
        """Return a dictionary with the container metadata for the domain."""

        self._can_read_container(user, account, container)
        if user != account:
            if until:
                raise NotAllowedError
        path, node = self._lookup_container(account, container)
        props = self._get_properties(node, until)
        mtime = props[self.MTIME]
        count, bytes, tstamp = self._get_statistics(node, until)
        tstamp = max(tstamp, mtime)
        if until is None:
            modified = tstamp
        else:
            modified = self._get_statistics(
                node)[2]  # Overall last modification.
            modified = max(modified, mtime)

        if user != account:
            meta = {'name': container}
        else:
            meta = {}
            if include_user_defined:
                if domain is None:
                    raise ValueError(
                        'Domain argument is obligatory for getting '
                        'user defined metadata')
                meta.update(
                    dict(self.node.attribute_get(props[self.SERIAL], domain)))
            if until is not None:
                meta.update({'until_timestamp': tstamp})
            meta.update({'name': container, 'count': count, 'bytes': bytes})
        meta.update({'modified': modified})
        return meta

    @debug_method
    @backend_method
    def update_container_meta(self, user, account, container, domain, meta,
                              replace=False):
        """Update the metadata associated with the container for the domain."""

        self._can_write_container(user, account, container)
        path, node = self._lookup_container(account, container)
        src_version_id, dest_version_id = self._put_metadata(
            user, node, domain, meta, replace,
            update_statistics_ancestors_depth=0)
        if src_version_id is not None:
            versioning = self._get_policy(
                node, is_account_policy=False)[VERSIONING_POLICY]
            if versioning != 'auto':
                self.node.version_remove(src_version_id,
                                         update_statistics_ancestors_depth=0)

    @debug_method
    @backend_method
    def get_container_policy(self, user, account, container):
        """Return a dictionary with the container policy."""

        self._can_read_container(user, account, container)
        if user != account:
            return {}
        path, node = self._lookup_container(account, container)
        return self._get_policy(node, is_account_policy=False)

    @debug_method
    @backend_method
    def update_container_policy(self, user, account, container, policy,
                                replace=False):
        """Update the policy associated with the container."""

        self._can_write_container(user, account, container)
        path, node = self._lookup_container(account, container)

        if PROJECT in policy:
            project = self._get_project(node)
            try:
                serial = self.astakosclient.issue_resource_reassignment(
                    holder=account,
                    to_source=policy[PROJECT],
                    provisions={(project, 'pithos.diskspace'):
                                    self.get_container_meta(
                            user, account, container,
                            include_user_defined=False)['bytes']})
            except BaseException, e:
                raise QuotaError(e)
            else:
                self.serials.append(serial)

        self._put_policy(node, policy, replace, is_account_policy=False,
                         default_project=account, check=True)

    @debug_method
    @backend_method
    def put_container(self, user, account, container, policy=None):
        """Create a new container with the given name."""

        policy = policy or {}
        self._can_write_container(user, account, container)
        try:
            path, node = self._lookup_container(account, container)
        except NameError:
            pass
        else:
            raise ContainerExists('Container already exists')
        path = '/'.join((account, container))
        node = self._put_path(
            user, self._lookup_account(account, True)[1], path,
            update_statistics_ancestors_depth=-1)
        self._put_policy(node, policy, True, is_account_policy=False,
                         default_project=account,
                         check=True if policy else False)

    @debug_method
    @backend_method
    def delete_container(self, user, account, container, until=None, prefix='',
                         delimiter=None, listing_limit=None):
        """Delete/purge the container with the given name."""

        self._can_write_container(user, account, container)
        path, node = self._lookup_container(account, container)
        project = self._get_project(node)

        if until is not None:
            hashes, size, serials = self.node.node_purge_children(
                node, until, CLUSTER_HISTORY,
                update_statistics_ancestors_depth=0)
            for h in hashes:
                self.store.map_delete(h)
            self.node.node_purge_children(node, until, CLUSTER_DELETED,
                                          update_statistics_ancestors_depth=0)
            if not self.free_versioning:
                self._report_size_change(
                    user, account, -size, project, {
                        'action': 'container purge',
                        'path': path,
                        'versions': ','.join(str(i) for i in serials)
                    }
                )
            return

        if not delimiter:
            if self._get_statistics(node)[0] > 0:
                raise ContainerNotEmpty('Container is not empty')
            hashes, size, serials = self.node.node_purge_children(
                node, inf, CLUSTER_HISTORY,
                update_statistics_ancestors_depth=0)
            for h in hashes:
                self.store.map_delete(h)
            self.node.node_purge_children(node, inf, CLUSTER_DELETED,
                                          update_statistics_ancestors_depth=0)
            self.node.node_remove(node, update_statistics_ancestors_depth=0)
            if not self.free_versioning:
                self._report_size_change(
                    user, account, -size, project, {
                        'action': 'container purge',
                        'path': path,
                        'versions': ','.join(str(i) for i in serials)
                    }
                )
        else:
            # remove only contents
            src_names = self._list_objects_no_limit(
                user, account, container, prefix='', delimiter=None,
                virtual=False, domain=None, keys=[], shared=False, until=None,
                size_range=None, all_props=True, public=False,
                listing_limit=listing_limit)
            paths = []
            for t in src_names:
                path = '/'.join((account, container, t[0]))
                node = t[2]
                if not self._exists(node):
                    continue
                src_version_id, dest_version_id = self._put_version_duplicate(
                    user, node, size=0, type='', hash=None, checksum='',
                    cluster=CLUSTER_DELETED,
                    update_statistics_ancestors_depth=1)
                del_size = self._apply_versioning(
                    account, container, src_version_id,
                    update_statistics_ancestors_depth=1)
                self._report_size_change(
                    user, account, -del_size, project, {
                        'action': 'object delete',
                        'path': path,
                        'versions': ','.join([str(dest_version_id)])})
                self._report_object_change(
                    user, account, path, details={'action': 'object delete'})
                paths.append(path)
            self.permissions.access_clear_bulk(paths)

        # remove all the cached allowed paths
        # removing the specific path could be more expensive
        self._reset_allowed_paths()

    def _list_objects(self, user, account, container, prefix, delimiter,
                      marker, limit, virtual, domain, keys, shared, until,
                      size_range, all_props, public):
        if user != account and until:
            raise NotAllowedError

        objects = set()
        if shared and public:
            # get shared first
            shared_paths = self._list_object_permissions(
                user, account, container, prefix, shared=True, public=False)
            if shared_paths:
                path, node = self._lookup_container(account, container)
                shared_paths = self._get_formatted_paths(shared_paths)
                objects = set(self._list_object_properties(
                    node, path, prefix, delimiter, marker, limit, virtual,
                    domain, keys, until, size_range, shared_paths, all_props))

            # get public
            objects |= set(self._list_public_object_properties(
                user, account, container, prefix, all_props))
            objects = list(objects)

            objects.sort(key=lambda x: x[0])
        elif public:
            objects = self._list_public_object_properties(
                user, account, container, prefix, all_props)
        else:
            allowed = self._list_object_permissions(
                user, account, container, prefix, shared, public=False)
            if shared and not allowed:
                return []
            path, node = self._lookup_container(account, container)
            allowed = self._get_formatted_paths(allowed)
            objects = self._list_object_properties(
                node, path, prefix, delimiter, marker, limit, virtual, domain,
                keys, until, size_range, allowed, all_props)

        # apply limits
        start, limit = self._list_limits(objects, marker, limit)
        return objects[start:start + limit]

    def _list_public_object_properties(self, user, account, container, prefix,
                                       all_props):
        public = self._list_object_permissions(
            user, account, container, prefix, shared=False, public=True)
        paths, nodes = self._lookup_objects(public)
        path = '/'.join((account, container))
        cont_prefix = path + '/'
        paths = [x[len(cont_prefix):] for x in paths]
        objects = [(p,) + props for p, props in
                   zip(paths, self.node.version_lookup_bulk(
                       nodes, all_props=all_props, order_by_path=True))]
        return objects

    def _list_objects_no_limit(self, user, account, container, prefix,
                               delimiter, virtual, domain, keys, shared, until,
                               size_range, all_props, public,
                               listing_limit=10000):
        objects = []
        while True:
            marker = objects[-1][0] if objects else None
            limit = listing_limit
            l = self._list_objects(
                user, account, container, prefix, delimiter, marker, limit,
                virtual, domain, keys, shared, until, size_range, all_props,
                public)
            objects.extend(l)
            if not l or len(l) < limit:
                break
        return objects

    def _list_object_permissions(self, user, account, container, prefix,
                                 shared, public):
        allowed = []
        path = '/'.join((account, container, prefix)).rstrip('/')
        if user != account:
            allowed = self.permissions.access_list_paths(user, path)
            if not allowed:
                raise NotAllowedError
        else:
            allowed = set()
            if shared:
                allowed.update(self.permissions.access_list_shared(path))
            if public:
                allowed.update(
                    [x[0] for x in self.permissions.public_list(path)])
            allowed = sorted(allowed)
            if not allowed:
                return []
        return allowed

    @debug_method
    @backend_method
    def list_objects(self, user, account, container, prefix='', delimiter=None,
                     marker=None, limit=10000, virtual=True, domain=None,
                     keys=None, shared=False, until=None, size_range=None,
                     public=False):
        """List (object name, object version_id) under a container."""

        keys = keys or []
        return self._list_objects(
            user, account, container, prefix, delimiter, marker, limit,
            virtual, domain, keys, shared, until, size_range, False, public)

    @debug_method
    @backend_method
    def list_object_meta(self, user, account, container, prefix='',
                         delimiter=None, marker=None, limit=10000,
                         virtual=True, domain=None, keys=None, shared=False,
                         until=None, size_range=None, public=False):
        """Return a list of metadata dicts of objects under a container."""

        keys = keys or []
        props = self._list_objects(
            user, account, container, prefix, delimiter, marker, limit,
            virtual, domain, keys, shared, until, size_range, True, public)
        objects = []
        for p in props:
            if len(p) == 2:
                objects.append({'subdir': p[0]})
            else:
                objects.append({
                    'name': p[0],
                    'bytes': p[self.SIZE + 1],
                    'type': p[self.TYPE + 1],
                    'hash': p[self.HASH + 1],
                    'version': p[self.SERIAL + 1],
                    'version_timestamp': p[self.MTIME + 1],
                    'modified': p[self.MTIME + 1] if until is None else None,
                    'modified_by': p[self.MUSER + 1],
                    'uuid': p[self.UUID + 1],
                    'checksum': p[self.CHECKSUM + 1],
                    'available': p[self.AVAILABLE + 1],
                    'map_check_timestamp': p[self.MAP_CHECK_TIMESTAMP + 1]})
        return objects

    @debug_method
    @backend_method
    def list_object_permissions(self, user, account, container, prefix=''):
        """Return a list of paths enforce permissions under a container."""

        return self._list_object_permissions(user, account, container, prefix,
                                             True, False)

    @debug_method
    @backend_method
    def list_object_public(self, user, account, container, prefix=''):
        """Return a mapping of object paths to public ids under a container."""

        public = {}
        for path, p in self.permissions.public_list('/'.join((account,
                                                              container,
                                                              prefix))):
            public[path] = p
        return public

    @debug_method
    @backend_method
    def get_object_meta(self, user, account, container, name, domain=None,
                        version=None, include_user_defined=True):
        """Return a dictionary with the object metadata for the domain."""

        self._can_read_object(user, account, container, name)
        path, node = self._lookup_object(account, container, name)
        props = self._get_version(node, version)
        if version is None:
            if not props[self.AVAILABLE]:
                try:
                    self._update_available(props)
                except (NotAllowedError, IllegalOperationError):
                    pass  # just update the database
                finally:
                    # get updated properties
                    props = self._get_version(node, version)
            modified = props[self.MTIME]
        else:
            try:
                modified = self._get_version(
                    node)[self.MTIME]  # Overall last modification.
            except NameError:  # Object may be deleted.
                del_props = self.node.version_lookup(
                    node, inf, CLUSTER_DELETED)
                if del_props is None:
                    raise ItemNotExists('Object does not exist')
                modified = del_props[self.MTIME]

        meta = {}
        if include_user_defined:
            if domain is None:
                raise ValueError(
                    'Domain argument is obligatory for getting '
                    'user defined metadata')
            meta.update(
                dict(self.node.attribute_get(props[self.SERIAL], domain)))
        meta.update({'name': name,
                     'bytes': props[self.SIZE],
                     'type': props[self.TYPE],
                     'hash': props[self.HASH],
                     'version': props[self.SERIAL],
                     'version_timestamp': props[self.MTIME],
                     'modified': modified,
                     'modified_by': props[self.MUSER],
                     'uuid': props[self.UUID],
                     'checksum': props[self.CHECKSUM],
                     'available': props[self.AVAILABLE],
                     'map_check_timestamp': props[self.MAP_CHECK_TIMESTAMP]})
        return meta

    @debug_method
    @backend_method
    def update_object_meta(self, user, account, container, name, domain, meta,
                           replace=False):
        """Update object metadata for a domain and return the new version."""

        self._can_write_object(user, account, container, name)

        path, node = self._lookup_object(account, container, name,
                                         lock_container=True)
        src_version_id, dest_version_id = self._put_metadata(
            user, node, domain, meta, replace,
            update_statistics_ancestors_depth=1)
        self._apply_versioning(account, container, src_version_id,
                               update_statistics_ancestors_depth=1)
        return dest_version_id

    @debug_method
    @backend_method
    def get_object_permissions_bulk(self, user, account, container, names):
        """Return the action allowed on the object, the path
        from which the object gets its permissions from,
        along with a dictionary containing the permissions."""

        permissions_path = self._get_permissions_path_bulk(account, container,
                                                           names)
        access_objects = self.permissions.access_check_bulk(permissions_path,
                                                            user)
        #group_parents = access_objects['group_parents']
        nobject_permissions = {}
        cpath = '/'.join((account, container, ''))
        cpath_idx = len(cpath)
        for path in permissions_path:
            allowed = 1
            name = path[cpath_idx:]
            if user != account:
                try:
                    allowed = access_objects[path]
                except KeyError:
                    raise NotAllowedError
            access_dict, allowed = \
                self.permissions.access_get_for_bulk(access_objects[path])
            nobject_permissions[name] = (self.ALLOWED[allowed], path,
                                         access_dict)
        self._lookup_objects(permissions_path)
        return nobject_permissions

    @debug_method
    @backend_method
    def get_object_permissions(self, user, account, container, name):
        """Return the action allowed on the object, the path
        from which the object gets its permissions from,
        along with a dictionary containing the permissions."""

        allowed = 'write'
        permissions_path = self._get_permissions_path(account, container, name)
        if user != account:
            if self.permissions.access_check(permissions_path, self.WRITE,
                                             user):
                allowed = 'write'
            elif self.permissions.access_check(permissions_path, self.READ,
                                               user):
                allowed = 'read'
            else:
                raise NotAllowedError
        self._lookup_object(account, container, name)
        return (allowed,
                permissions_path,
                self.permissions.access_get(permissions_path))

    @debug_method
    @backend_method
    def update_object_permissions(self, user, account, container, name,
                                  permissions):
        """Update the permissions associated with the object."""

        if user != account:
            raise NotAllowedError
        path = self._lookup_object(account, container, name,
                                   lock_container=True)[0]
        self._check_permissions(path, permissions)
        try:
            self.permissions.access_set(path, permissions)
        except:
            raise ValueError
        else:
            self._report_sharing_change(user, account, path, {'members':
                                        self.permissions.access_members(path)})

        # remove all the cached allowed paths
        # filtering out only those affected could be more expensive
        self._reset_allowed_paths()

    @debug_method
    @backend_method
    def get_object_public(self, user, account, container, name):
        """Return the public id of the object if applicable."""

        self._can_read_object(user, account, container, name)
        path = self._lookup_object(account, container, name)[0]
        p = self.permissions.public_get(path)
        return p

    @debug_method
    @backend_method
    def update_object_public(self, user, account, container, name, public):
        """Update the public status of the object."""

        self._can_write_object(user, account, container, name)
        path = self._lookup_object(account, container, name,
                                   lock_container=True)[0]
        if not public:
            self.permissions.public_unset(path)
        else:
            self.permissions.public_set(
                path, self.public_url_security, self.public_url_alphabet)

    def _update_available(self, props):
        """Checks if the object map exists and updates the database"""

        if not props[self.AVAILABLE]:
            if props[self.MAP_CHECK_TIMESTAMP]:
                elapsed_time = time() - float(props[self.MAP_CHECK_TIMESTAMP])
                if elapsed_time < self.map_check_interval:
                    raise NotAllowedError(
                        'Consequent map checks are limited: retry later.')
        try:
            hashmap = self.store.map_get_archipelago(props[self.HASH],
                                                     props[self.SIZE])
        except:  # map does not exist
            # Raising an exception results in db transaction rollback
            # However we have to force the update of the database
            self.wrapper.rollback()  # rollback existing transaction
            self.wrapper.execute()  # start new transaction
            self.node.version_put_property(props[self.SERIAL],
                                           'map_check_timestamp', time())
            self.wrapper.commit()  # commit transaction
            self.wrapper.execute()  # start new transaction
            raise IllegalOperationError(
                'Unable to retrieve Archipelago Volume hashmap.')
        else:  # map exists
            self.node.version_put_property(props[self.SERIAL],
                                           'available', True)
            self.node.version_put_property(props[self.SERIAL],
                                           'map_check_timestamp', time())
            return hashmap

    @debug_method
    @backend_method
    def get_object_hashmap(self, user, account, container, name, version=None):
        """Return the object's size and a list with partial hashes."""

        self._can_read_object(user, account, container, name)
        path, node = self._lookup_object(account, container, name)
        props = self._get_version(node, version)
        if props[self.HASH] is None:
            return 0, ()
        if props[self.HASH].startswith('archip:'):
            hashmap = self._update_available(props)
            return props[self.SIZE], [x for x in hashmap]
        else:
            hashmap = self.store.map_get(self._unhexlify_hash(
                props[self.HASH]))
            return props[self.SIZE], [binascii.hexlify(x) for x in hashmap]

    def _update_object_hash(self, user, account, container, name, size, type,
                            hash, checksum, domain, meta, replace_meta,
                            permissions, src_node=None, src_version_id=None,
                            is_copy=False, report_size_change=True,
                            available=True):
        if permissions is not None and user != account:
            raise NotAllowedError
        self._can_write_object(user, account, container, name)
        if permissions is not None:
            path = '/'.join((account, container, name))
            self._check_permissions(path, permissions)

        account_path, account_node = self._lookup_account(account, True)
        container_path, container_node = self._lookup_container(
            account, container)
        project = self._get_project(container_node)

        path, node = self._put_object_node(
            container_path, container_node, name)
        pre_version_id, dest_version_id = self._put_version_duplicate(
            user, node, src_node=src_node, size=size, type=type, hash=hash,
            checksum=checksum, is_copy=is_copy,
            update_statistics_ancestors_depth=1,
            available=available, keep_available=False)

        # Handle meta.
        if src_version_id is None:
            src_version_id = pre_version_id
        self._put_metadata_duplicate(
            src_version_id, dest_version_id, domain, node, meta, replace_meta)

        del_size = self._apply_versioning(account, container, pre_version_id,
                                          update_statistics_ancestors_depth=1)
        size_delta = size - del_size
        if size_delta > 0:
            # Check account quota.
            if not self.using_external_quotaholder:
                account_quota = long(self._get_policy(
                    account_node, is_account_policy=True)[QUOTA_POLICY])
                account_usage = self._get_statistics(account_node,
                                                     compute=True)[1]
                if (account_quota > 0 and account_usage > account_quota):
                    raise QuotaError(
                        'Account quota exceeded: limit: %s, usage: %s' % (
                            account_quota, account_usage))

            # Check container quota.
            container_quota = long(self._get_policy(
                container_node, is_account_policy=False)[QUOTA_POLICY])
            container_usage = self._get_statistics(container_node)[1]
            if (container_quota > 0 and container_usage > container_quota):
                # This must be executed in a transaction, so the version is
                # never created if it fails.
                raise QuotaError(
                    'Container quota exceeded: limit: %s, usage: %s' % (
                        container_quota, container_usage
                    )
                )

        if report_size_change:
            self._report_size_change(
                user, account, size_delta, project,
                {'action': 'object update', 'path': path,
                 'versions': ','.join([str(dest_version_id)])})
        if permissions is not None:
            self.permissions.access_set(path, permissions)
            self._report_sharing_change(
                user, account, path,
                {'members': self.permissions.access_members(path)})

        self._report_object_change(
            user, account, path,
            details={'version': dest_version_id, 'action': 'object update'})
        return dest_version_id

    @debug_method
    @backend_method
    def register_object_map(self, user, account, container, name, size, type,
                            mapfile, checksum='', domain='pithos', meta=None,
                            replace_meta=False, permissions=None):
        """Register an object mapfile without providing any data.

        Lock the container path, create a node pointing to the object path,
        create a version pointing to the mapfile
        and issue the size change in the quotaholder.

        :param user: the user account which performs the action

        :param account: the account under which the object resides

        :param container: the container under which the object resides

        :param name: the object name

        :param size: the object size

        :param type: the object mimetype

        :param mapfile: the mapfile pointing to the object data

        :param checkcum: the md5 checksum (optional)

        :param domain: the object domain

        :param meta: a dict with custom object metadata

        :param replace_meta: replace existing metadata or not

        :param permissions: a dict with the read and write object permissions

        :returns: the new object uuid

        :raises: ItemNotExists, NotAllowedError, QuotaError
        """

        meta = meta or {}
        try:
            self.lock_container_path = True
            self.put_container(user, account, container, policy=None)
        except ContainerExists:
            pass
        finally:
            self.lock_container_path = False
        dest_version_id = self._update_object_hash(
            user, account, container, name, size, type, mapfile, checksum,
            domain, meta, replace_meta, permissions, available=False)
        return self.node.version_get_properties(dest_version_id,
                                                keys=('uuid',))[0]

    @debug_method
    def update_object_hashmap(self, user, account, container, name, size, type,
                              hashmap, checksum, domain, meta=None,
                              replace_meta=False, permissions=None):
        """Create/update an object's hashmap and return the new version."""

        for h in hashmap:
            if h.startswith('archip_'):
                raise IllegalOperationError(
                    'Cannot update Archipelago Volume hashmap.')
        meta = meta or {}
        if size == 0:  # No such thing as an empty hashmap.
            hashmap = [self.put_block('')]
        map = HashMap(self.block_size, self.hash_algorithm)
        map.extend([self._unhexlify_hash(x) for x in hashmap])
        missing = self.store.block_search(map)
        if missing:
            ie = IndexError()
            ie.data = [binascii.hexlify(x) for x in missing]
            raise ie

        hash = map.hash()
        hexlified = binascii.hexlify(hash)
        # _update_object_hash() locks destination path
        dest_version_id = self._update_object_hash(
            user, account, container, name, size, type, hexlified, checksum,
            domain, meta, replace_meta, permissions)
        self.store.map_put(hash, map)
        return dest_version_id, hexlified

    @debug_method
    @backend_method
    def update_object_checksum(self, user, account, container, name, version,
                               checksum):
        """Update an object's checksum."""

        # Update objects with greater version and same hashmap
        # and size (fix metadata updates).
        self._can_write_object(user, account, container, name)
        path, node = self._lookup_object(account, container, name,
                                         lock_container=True)
        props = self._get_version(node, version)
        versions = self.node.node_get_versions(node)
        for x in versions:
            if (x[self.SERIAL] >= int(version) and
                x[self.HASH] == props[self.HASH] and
                    x[self.SIZE] == props[self.SIZE]):
                self.node.version_put_property(
                    x[self.SERIAL], 'checksum', checksum)

    def _copy_object(self, user, src_account, src_container, src_name,
                     dest_account, dest_container, dest_name, type,
                     dest_domain=None, dest_meta=None, replace_meta=False,
                     permissions=None, src_version=None, is_move=False,
                     delimiter=None, listing_limit=10000):

        dest_meta = dest_meta or {}
        dest_version_ids = []
        self._can_read_object(user, src_account, src_container, src_name)

        src_container_path = '/'.join((src_account, src_container))
        dest_container_path = '/'.join((dest_account, dest_container))
        # Lock container paths in alphabetical order
        if src_container_path < dest_container_path:
            src_container_node = self._lookup_container(src_account,
                                                        src_container)[-1]
            dest_container_node = self._lookup_container(dest_account,
                                                         dest_container)[-1]
        else:
            dest_container_node = self._lookup_container(dest_account,
                                                         dest_container)[-1]
            src_container_node = self._lookup_container(src_account,
                                                        src_container)[-1]

        cross_account = src_account != dest_account
        cross_container = src_container != dest_container
        if not cross_account and cross_container:
            src_project = self._get_project(src_container_node)
            dest_project = self._get_project(dest_container_node)
            cross_project = src_project != dest_project
        else:
            cross_project = False
        report_size_change = not is_move or cross_account or cross_project

        path, node = self._lookup_object(src_account, src_container, src_name)
        # TODO: Will do another fetch of the properties in duplicate version...
        props = self._get_version(
            node, src_version)  # Check to see if source exists.
        src_version_id = props[self.SERIAL]
        hash = props[self.HASH]
        size = props[self.SIZE]
        is_copy = not is_move and (src_account, src_container, src_name) != (
            dest_account, dest_container, dest_name)  # New uuid.
        dest_version_ids.append(self._update_object_hash(
            user, dest_account, dest_container, dest_name, size, type, hash,
            None, dest_domain, dest_meta, replace_meta, permissions,
            src_node=node, src_version_id=src_version_id, is_copy=is_copy,
            report_size_change=report_size_change))
        if is_move and ((src_account, src_container, src_name) !=
                        (dest_account, dest_container, dest_name)):
            self._delete_object(user, src_account, src_container, src_name,
                                report_size_change=report_size_change)

        if delimiter:
            prefix = (src_name + delimiter if not
                      src_name.endswith(delimiter) else src_name)
            src_names = self._list_objects_no_limit(
                user, src_account, src_container, prefix, delimiter=None,
                virtual=False, domain=None, keys=[], shared=False, until=None,
                size_range=None, all_props=True, public=False,
                listing_limit=listing_limit)
            src_names.sort(key=lambda x: x[2])  # order by nodes
            paths = [elem[0] for elem in src_names]
            nodes = [elem[2] for elem in src_names]
            # TODO: Will do another fetch of the properties
            # in duplicate version...
            props = self._get_versions(nodes)  # Check to see if source exists.

            for prop, path, node in zip(props, paths, nodes):
                src_version_id = prop[self.SERIAL]
                hash = prop[self.HASH]
                vtype = prop[self.TYPE]
                size = prop[self.SIZE]
                dest_prefix = dest_name + delimiter if not dest_name.endswith(
                    delimiter) else dest_name
                vdest_name = path.replace(prefix, dest_prefix, 1)
                # _update_object_hash() locks destination path
                dest_version_ids.append(self._update_object_hash(
                    user, dest_account, dest_container, vdest_name, size,
                    vtype, hash, None, dest_domain, meta={},
                    replace_meta=False, permissions=None, src_node=node,
                    src_version_id=src_version_id, is_copy=is_copy,
                    report_size_change=report_size_change))
                if is_move and ((src_account, src_container, src_name) !=
                                (dest_account, dest_container, dest_name)):
                    self._delete_object(user, src_account, src_container, path,
                                        report_size_change=report_size_change)
        return (dest_version_ids[0] if len(dest_version_ids) == 1 else
                dest_version_ids)

    @debug_method
    @backend_method
    def copy_object(self, user, src_account, src_container, src_name,
                    dest_account, dest_container, dest_name, type, domain,
                    meta=None, replace_meta=False, permissions=None,
                    src_version=None, delimiter=None, listing_limit=None):
        """Copy an object's data and metadata."""

        meta = meta or {}
        dest_version_id = self._copy_object(
            user, src_account, src_container, src_name, dest_account,
            dest_container, dest_name, type, domain, meta, replace_meta,
            permissions, src_version, False, delimiter,
            listing_limit=listing_limit)
        return dest_version_id

    @debug_method
    @backend_method
    def move_object(self, user, src_account, src_container, src_name,
                    dest_account, dest_container, dest_name, type, domain,
                    meta=None, replace_meta=False, permissions=None,
                    delimiter=None, listing_limit=None):
        """Move an object's data and metadata."""

        meta = meta or {}
        if user != src_account:
            raise NotAllowedError
        dest_version_id = self._move_object(
            user, src_account, src_container, src_name, dest_account,
            dest_container, dest_name, type, domain, meta, replace_meta,
            permissions, None, delimiter=delimiter,
            listing_limit=listing_limit)
        return dest_version_id

    def _delete_object(self, user, account, container, name, until=None,
                       delimiter=None, report_size_change=True,
                       listing_limit=None):
        if user != account:
            raise NotAllowedError

        # lock container path
        container_path, container_node = self._lookup_container(account,
                                                                container)
        project = self._get_project(container_node)
        path, node = self._lookup_object(account, container, name)

        if until is not None:
            if node is None:
                return
            hashes = []
            size = 0
            serials = []
            h, s, v = self.node.node_purge(node, until, CLUSTER_NORMAL,
                                           update_statistics_ancestors_depth=1)
            hashes += h
            size += s
            serials += v
            h, s, v = self.node.node_purge(node, until, CLUSTER_HISTORY,
                                           update_statistics_ancestors_depth=1)
            hashes += h
            if not self.free_versioning:
                size += s
            serials += v
            for h in hashes:
                self.store.map_delete(h)
            self.node.node_purge(node, until, CLUSTER_DELETED,
                                 update_statistics_ancestors_depth=1)
            try:
                self._get_version(node)
            except NameError:
                self.permissions.access_clear(path)
            self._report_size_change(
                user, account, -size, project, {
                    'action': 'object purge',
                    'path': path,
                    'versions': ','.join(str(i) for i in serials)
                }
            )
            return

        if not self._exists(node):
            raise ItemNotExists('Object is deleted.')

        src_version_id, dest_version_id = self._put_version_duplicate(
            user, node, size=0, type='', hash=None, checksum='',
            cluster=CLUSTER_DELETED, update_statistics_ancestors_depth=1)
        del_size = self._apply_versioning(account, container, src_version_id,
                                          update_statistics_ancestors_depth=1)
        if report_size_change:
            self._report_size_change(
                user, account, -del_size, project,
                {'action': 'object delete',
                 'path': path,
                 'versions': ','.join([str(dest_version_id)])})
        self._report_object_change(
            user, account, path, details={'action': 'object delete'})
        self.permissions.access_clear(path)

        if delimiter:
            prefix = name + delimiter if not name.endswith(delimiter) else name
            src_names = self._list_objects_no_limit(
                user, account, container, prefix, delimiter=None,
                virtual=False, domain=None, keys=[], shared=False, until=None,
                size_range=None, all_props=True, public=False,
                listing_limit=listing_limit)
            paths = []
            for t in src_names:
                path = '/'.join((account, container, t[0]))
                node = t[2]
                if not self._exists(node):
                    continue
                src_version_id, dest_version_id = self._put_version_duplicate(
                    user, node, size=0, type='', hash=None, checksum='',
                    cluster=CLUSTER_DELETED,
                    update_statistics_ancestors_depth=1)
                del_size = self._apply_versioning(
                    account, container, src_version_id,
                    update_statistics_ancestors_depth=1)
                if report_size_change:
                    self._report_size_change(
                        user, account, -del_size, project,
                        {'action': 'object delete',
                         'path': path,
                         'versions': ','.join([str(dest_version_id)])})
                self._report_object_change(
                    user, account, path, details={'action': 'object delete'})
                paths.append(path)
            self.permissions.access_clear_bulk(paths)

        # remove all the cached allowed paths
        # removing the specific path could be more expensive
        self._reset_allowed_paths()

    @debug_method
    @backend_method
    def delete_object(self, user, account, container, name, until=None,
                      prefix='', delimiter=None, listing_limit=None):
        """Delete/purge an object."""

        self._delete_object(user, account, container, name, until, delimiter,
                            listing_limit=listing_limit)

    @debug_method
    @backend_method
    def list_versions(self, user, account, container, name):
        """Return a list of all object (version, version_timestamp) tuples."""

        self._can_read_object(user, account, container, name)
        path, node = self._lookup_object(account, container, name)
        versions = self.node.node_get_versions(node)
        return [[x[self.SERIAL], x[self.MTIME]] for x in versions if
                x[self.CLUSTER] != CLUSTER_DELETED]

    @debug_method
    @backend_method
    def get_uuid(self, user, uuid, check_permissions=True):
        """Return the (account, container, name) for the UUID given."""

        info = self.node.latest_uuid(uuid, CLUSTER_NORMAL)
        if info is None:
            raise NameError
        path, serial = info
        account, container, name = path.split('/', 2)
        if check_permissions:
            self._can_read_object(user, account, container, name)
        return (account, container, name)

    @debug_method
    @backend_method
    def get_public(self, user, public):
        """Return the (account, container, name) for the public id given."""

        path = self.permissions.public_path(public)
        if path is None:
            raise NameError
        account, container, name = path.split('/', 2)
        self._can_read_object(user, account, container, name)
        return (account, container, name)

    def get_block(self, hash):
        """Return a block's data."""

        logger.debug("get_block: %s", hash)
        if hash.startswith('archip_'):
            block = self.store.block_get_archipelago(hash)
        else:
            block = self.store.block_get(self._unhexlify_hash(hash))
        if not block:
            raise ItemNotExists('Block does not exist')
        return block

    def put_block(self, data):
        """Store a block and return the hash."""

        logger.debug("put_block: %s", len(data))
        return binascii.hexlify(self.store.block_put(data))

    def update_block(self, hash, data, offset=0):
        """Update a known block and return the hash."""

        logger.debug("update_block: %s %s %s", hash, len(data), offset)
        if hash.startswith('archip_'):
            raise IllegalOperationError(
                'Cannot update an Archipelago Volume block.')
        if offset == 0 and len(data) == self.block_size:
            return self.put_block(data)
        h = self.store.block_update(self._unhexlify_hash(hash), offset, data)
        return binascii.hexlify(h)

    # Path functions.

    def _generate_uuid(self):
        return str(uuidlib.uuid4())

    def _put_object_node(self, path, parent, name):
        path = '/'.join((path, name))
        node = self.node.node_lookup(path)
        if node is None:
            node = self.node.node_create(parent, path)
        return path, node

    def _put_path(self, user, parent, path,
                  update_statistics_ancestors_depth=None):
        node = self.node.node_create(parent, path)
        self.node.version_create(node, None, 0, '', None, user,
                                 self._generate_uuid(), '', CLUSTER_NORMAL,
                                 update_statistics_ancestors_depth)
        return node

    def _lookup_account(self, account, create=True):
        node = self.node.node_lookup(account)
        if node is None and create:
            node = self._put_path(
                account, self.ROOTNODE, account,
                update_statistics_ancestors_depth=-1)  # User is account.
        return account, node

    def _lookup_container(self, account, container):
        for_update = True if self.lock_container_path else False
        path = '/'.join((account, container))
        node = self.node.node_lookup(path, for_update)
        if node is None:
            raise ItemNotExists('Container does not exist')
        return path, node

    def _lookup_object(self, account, container, name, lock_container=False):
        if lock_container:
            self._lookup_container(account, container)

        path = '/'.join((account, container, name))
        node = self.node.node_lookup(path)
        if node is None:
            raise ItemNotExists('Object does not exist')
        return path, node

    def _lookup_objects(self, paths):
        nodes = self.node.node_lookup_bulk(paths)
        return paths, nodes

    def _get_properties(self, node, until=None):
        """Return properties until the timestamp given."""

        before = until if until is not None else inf
        props = self.node.version_lookup(node, before, CLUSTER_NORMAL)
        if props is None and until is not None:
            props = self.node.version_lookup(node, before, CLUSTER_HISTORY)
        if props is None:
            raise ItemNotExists('Path does not exist')
        return props

    def _get_statistics(self, node, until=None, compute=False):
        """Return (count, sum of size, timestamp) of everything under node."""

        if until is not None:
            stats = self.node.statistics_latest(node, until, CLUSTER_DELETED)
        elif compute:
            stats = self.node.statistics_latest(node,
                                                except_cluster=CLUSTER_DELETED)
        else:
            stats = self.node.statistics_get(node, CLUSTER_NORMAL)
        if stats is None:
            stats = (0, 0, 0)
        return stats

    def _get_version(self, node, version=None):
        if version is None:
            props = self.node.version_lookup(node, inf, CLUSTER_NORMAL)
            if props is None:
                raise ItemNotExists('Object does not exist')
        else:
            try:
                version = int(version)
            except ValueError:
                raise VersionNotExists('Version does not exist')
            props = self.node.version_get_properties(version, node=node)
            if props is None or props[self.CLUSTER] == CLUSTER_DELETED:
                raise VersionNotExists('Version does not exist')
        return props

    def _get_versions(self, nodes):
        return self.node.version_lookup_bulk(nodes, inf, CLUSTER_NORMAL)

    def _put_version_duplicate(self, user, node, src_node=None, size=None,
                               type=None, hash=None, checksum=None,
                               cluster=CLUSTER_NORMAL, is_copy=False,
                               update_statistics_ancestors_depth=None,
                               available=True, keep_available=True):
        """Create a new version of the node."""

        props = self.node.version_lookup(
            node if src_node is None else src_node, inf, CLUSTER_NORMAL)
        if props is not None:
            src_version_id = props[self.SERIAL]
            src_hash = props[self.HASH]
            src_size = props[self.SIZE]
            src_type = props[self.TYPE]
            src_checksum = props[self.CHECKSUM]
            if keep_available:
                src_available = props[self.AVAILABLE]
                src_map_check_timestamp = props[self.MAP_CHECK_TIMESTAMP]
            else:
                src_available = available
                src_map_check_timestamp = None
        else:
            src_version_id = None
            src_hash = None
            src_size = 0
            src_type = ''
            src_checksum = ''
            src_available = available
            src_map_check_timestamp = None
        if size is None:  # Set metadata.
            hash = src_hash  # This way hash can be set to None
                             # (account or container).
            size = src_size
        if type is None:
            type = src_type
        if checksum is None:
            checksum = src_checksum
        uuid = self._generate_uuid(
        ) if (is_copy or src_version_id is None) else props[self.UUID]

        if src_node is None:
            pre_version_id = src_version_id
        else:
            pre_version_id = None
            props = self.node.version_lookup(node, inf, CLUSTER_NORMAL)
            if props is not None:
                pre_version_id = props[self.SERIAL]
        if pre_version_id is not None:
            self.node.version_recluster(pre_version_id, CLUSTER_HISTORY,
                                        update_statistics_ancestors_depth)

        dest_version_id, mtime = self.node.version_create(
            node, hash, size, type, src_version_id, user, uuid, checksum,
            cluster, update_statistics_ancestors_depth,
            available=src_available,
            map_check_timestamp=src_map_check_timestamp)

        self.node.attribute_unset_is_latest(node, dest_version_id)

        return pre_version_id, dest_version_id

    def _put_metadata_duplicate(self, src_version_id, dest_version_id, domain,
                                node, meta, replace=False):
        if src_version_id is not None:
            self.node.attribute_copy(src_version_id, dest_version_id)
        if not replace:
            self.node.attribute_del(dest_version_id, domain, (
                k for k, v in meta.iteritems() if v == ''))
            self.node.attribute_set(dest_version_id, domain, node, (
                (k, v) for k, v in meta.iteritems() if v != ''))
        else:
            self.node.attribute_del(dest_version_id, domain)
            self.node.attribute_set(dest_version_id, domain, node, ((
                k, v) for k, v in meta.iteritems()))

    def _put_metadata(self, user, node, domain, meta, replace=False,
                      update_statistics_ancestors_depth=None):
        """Create a new version and store metadata."""

        src_version_id, dest_version_id = self._put_version_duplicate(
            user, node,
            update_statistics_ancestors_depth=
            update_statistics_ancestors_depth)
        self._put_metadata_duplicate(
            src_version_id, dest_version_id, domain, node, meta, replace)
        return src_version_id, dest_version_id

    def _list_limits(self, listing, marker, limit):
        start = 0
        if marker:
            try:
                start = listing.index(marker) + 1
            except ValueError:
                pass
        if not limit or limit > 10000:
            limit = 10000
        return start, limit

    def _list_object_properties(self, parent, path, prefix='', delimiter=None,
                                marker=None, limit=10000, virtual=True,
                                domain=None, keys=None, until=None,
                                size_range=None, allowed=None,
                                all_props=False):
        keys = keys or []
        allowed = allowed or []
        cont_prefix = path + '/'
        prefix = cont_prefix + prefix
        start = cont_prefix + marker if marker else None
        before = until if until is not None else inf
        filterq = keys if domain else []
        sizeq = size_range

        objects, prefixes = self.node.latest_version_list(
            parent, prefix, delimiter, start, limit, before, CLUSTER_DELETED,
            allowed, domain, filterq, sizeq, all_props)
        objects.extend([(p, None) for p in prefixes] if virtual else [])
        objects.sort(key=lambda x: x[0])
        objects = [(x[0][len(cont_prefix):],) + x[1:] for x in objects]
        return objects

    # Reporting functions.

    @debug_method
    @backend_method
    def _report_size_change(self, user, account, size, source, details=None):
        details = details or {}

        if size == 0:
            return

        account_node = self._lookup_account(account, True)[1]
        total = self._get_statistics(account_node, compute=True)[1]
        details.update({'user': user, 'total': total})
        self.messages.append(
            (QUEUE_MESSAGE_KEY_PREFIX % ('resource.diskspace',),
             account, QUEUE_INSTANCE_ID, 'diskspace', float(size), details))

        if not self.using_external_quotaholder:
            return

        try:
            name = details['path'] if 'path' in details else ''
            serial = self.astakosclient.issue_one_commission(
                holder=account,
                provisions={(source, 'pithos.diskspace'): size},
                name=name)
        except BaseException, e:
            raise QuotaError(e)
        else:
            self.serials.append(serial)

    @debug_method
    @backend_method
    def _report_object_change(self, user, account, path, details=None):
        details = details or {}
        details.update({'user': user})
        self.messages.append((QUEUE_MESSAGE_KEY_PREFIX % ('object',),
                              account, QUEUE_INSTANCE_ID, 'object', path,
                              details))

    @debug_method
    @backend_method
    def _report_sharing_change(self, user, account, path, details=None):
        details = details or {}
        details.update({'user': user})
        self.messages.append((QUEUE_MESSAGE_KEY_PREFIX % ('sharing',),
                              account, QUEUE_INSTANCE_ID, 'sharing', path,
                              details))

    # Policy functions.

    def _check_project(self, value):
        # raise ValueError('Bad quota source policy')
        pass

    def _check_policy(self, policy):
        for k, v in policy.iteritems():
            if k == QUOTA_POLICY:
                q = int(v)  # May raise ValueError.
                if q < 0:
                    raise ValueError
            elif k == VERSIONING_POLICY:
                if v not in ['auto', 'none']:
                    raise ValueError
            elif k == PROJECT:
                self._check_project(v)
            else:
                raise ValueError

    def _get_default_policy(self, node=None, is_account_policy=True,
                            default_project=None):
        if is_account_policy:
            default_policy = self.default_account_policy
        else:
            default_policy = self.default_container_policy
            if default_project is None and node is not None:
                # set container's account as the default quota source
                default_project = self.node.node_get_parent_path(node)
            default_policy[PROJECT] = default_project
        return default_policy

    def _put_policy(self, node, policy, replace,
                    is_account_policy=True, default_project=None,
                    check=True):
        default_policy = self._get_default_policy(node,
                                                  is_account_policy,
                                                  default_project)
        if replace:
            for k, v in default_policy.iteritems():
                if k not in policy:
                    policy[k] = v
        if check:
            self._check_policy(policy)

        self.node.policy_set(node, policy)

    def _get_policy(self, node, is_account_policy=True,
                    default_project=None):
        default_policy = self._get_default_policy(node,
                                                  is_account_policy,
                                                  default_project)
        policy = default_policy.copy()
        policy.update(self.node.policy_get(node))
        return policy

    def _get_project(self, node):
        policy = self._get_policy(node, is_account_policy=False)
        return policy[PROJECT]

    def _apply_versioning(self, account, container, version_id,
                          update_statistics_ancestors_depth=None):
        """Delete the provided version if such is the policy.
           Return size of object removed.
        """

        if version_id is None:
            return 0
        path, node = self._lookup_container(account, container)
        versioning = self._get_policy(
            node, is_account_policy=False)[VERSIONING_POLICY]
        if versioning != 'auto':
            hash, size = self.node.version_remove(
                version_id, update_statistics_ancestors_depth)
            self.store.map_delete(hash)
            return size
        elif self.free_versioning:
            return self.node.version_get_properties(
                version_id, keys=('size',))[0]
        return 0

    # Access control functions.

    def _check_groups(self, groups):
        # raise ValueError('Bad characters in groups')
        pass

    def _check_permissions(self, path, permissions):
        # raise ValueError('Bad characters in permissions')
        pass

    def _get_formatted_paths(self, paths):
        formatted = []
        if len(paths) == 0:
            return formatted
        props = self.node.get_props(paths)
        if props:
            for prop in props:
                if prop[1].split(';', 1)[0].strip() in (
                        'application/directory', 'application/folder'):
                    formatted.append((prop[0].rstrip('/') + '/',
                                      self.MATCH_PREFIX))
                formatted.append((prop[0], self.MATCH_EXACT))
        return formatted

    def _get_permissions_path(self, account, container, name):
        path = '/'.join((account, container, name))
        permission_paths = self.permissions.access_inherit(path)
        permission_paths.sort()
        permission_paths.reverse()
        for p in permission_paths:
            if p == path:
                return p
            else:
                if p.count('/') < 2:
                    continue
                node = self.node.node_lookup(p)
                props = None
                if node is not None:
                    props = self.node.version_lookup(node, inf, CLUSTER_NORMAL)
                if props is not None:
                    if props[self.TYPE].split(';', 1)[0].strip() in (
                            'application/directory', 'application/folder'):
                        return p
        return None

    def _get_permissions_path_bulk(self, account, container, names):
        formatted_paths = []
        for name in names:
            path = '/'.join((account, container, name))
            formatted_paths.append(path)
        permission_paths = self.permissions.access_inherit_bulk(
            formatted_paths)
        permission_paths.sort()
        permission_paths.reverse()
        permission_paths_list = []
        lookup_list = []
        for p in permission_paths:
            if p in formatted_paths:
                permission_paths_list.append(p)
            else:
                if p.count('/') < 2:
                    continue
                lookup_list.append(p)

        if len(lookup_list) > 0:
            props = self.node.get_props(lookup_list)
            if props:
                for prop in props:
                    if prop[1].split(';', 1)[0].strip() in (
                            'application/directory', 'application/folder'):
                        permission_paths_list.append(prop[0])

        if len(permission_paths_list) > 0:
            return permission_paths_list

        return None

    def _reset_allowed_paths(self):
        self.read_allowed_paths = defaultdict(set)
        self.write_allowed_paths = defaultdict(set)

    @check_allowed_paths(action=0)
    def _can_read_account(self, user, account):
        if user != account:
            if account not in self._allowed_accounts(user):
                raise NotAllowedError

    @check_allowed_paths(action=1)
    def _can_write_account(self, user, account):
        if user != account:
            raise NotAllowedError

    @check_allowed_paths(action=0)
    def _can_read_container(self, user, account, container):
        if user != account:
            if container not in self._allowed_containers(user, account):
                raise NotAllowedError

    @check_allowed_paths(action=1)
    def _can_write_container(self, user, account, container):
        if user != account:
            raise NotAllowedError

    def can_write_container(self, user, account, container):
        return self._can_write_container(user, account, container)

    @check_allowed_paths(action=0)
    def _can_read_object(self, user, account, container, name):
        if user == account:
            return True
        path = '/'.join((account, container, name))
        if self.permissions.public_get(path) is not None:
            return True
        path = self._get_permissions_path(account, container, name)
        if not path:
            raise NotAllowedError
        if (not self.permissions.access_check(path, self.READ, user) and not
                self.permissions.access_check(path, self.WRITE, user)):
            raise NotAllowedError

    @check_allowed_paths(action=1)
    def _can_write_object(self, user, account, container, name):
        if user == account:
            return True
        path = '/'.join((account, container, name))
        path = self._get_permissions_path(account, container, name)
        if not path:
            raise NotAllowedError
        if not self.permissions.access_check(path, self.WRITE, user):
            raise NotAllowedError

    def _allowed_accounts(self, user):
        allow = set()
        for path in self.permissions.access_list_paths(user):
            p = path.split('/', 1)[0]
            allow.add(p)
        self.read_allowed_paths[user] |= allow
        return sorted(allow)

    def _allowed_containers(self, user, account):
        allow = set()
        for path in self.permissions.access_list_paths(user, account):
            p = path.split('/', 2)[1]
            allow.add(p)
        self.read_allowed_paths[user] |= allow
        return sorted(allow)

    # Domain functions

    @debug_method
    @backend_method
    def get_domain_objects(self, domain, user=None):
        allowed_paths = self.permissions.access_list_paths(
            user, include_owned=user is not None, include_containers=False)
        if not allowed_paths:
            return []
        obj_list = self.node.domain_object_list(
            domain, allowed_paths, CLUSTER_NORMAL)
        return [(path,
                 self._build_metadata(props, user_defined_meta),
                 self.permissions.access_get(path)) for
                path, props, user_defined_meta in obj_list]

    # util functions

    def _build_metadata(self, props, user_defined=None,
                        include_user_defined=True):
        meta = {'bytes': props[self.SIZE],
                'type': props[self.TYPE],
                'hash': props[self.HASH],
                'version': props[self.SERIAL],
                'version_timestamp': props[self.MTIME],
                'modified_by': props[self.MUSER],
                'uuid': props[self.UUID],
                'checksum': props[self.CHECKSUM]}
        if include_user_defined and user_defined is not None:
            meta.update(user_defined)
        return meta

    def _exists(self, node):
        try:
            self._get_version(node)
        except ItemNotExists:
            return False
        else:
            return True

    def _unhexlify_hash(self, hash):
        try:
            return binascii.unhexlify(hash)
        except TypeError:
            raise InvalidHash(hash)
