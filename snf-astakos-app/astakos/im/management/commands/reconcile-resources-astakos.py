# Copyright 2013-2014 GRNET S.A. All rights reserved.
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
from datetime import datetime

from snf_django.management.commands import SynnefoCommand, CommandError
from django.db import transaction
from snf_django.utils import reconcile
from snf_django.management.utils import pprint_table
from astakos.im.models import Component, AstakosUser
from astakos.im import quotas
from astakos.im.functions import count_pending_app
import astakos.quotaholder_app.callpoint as qh
import astakos.quotaholder_app.exception as qh_exception


class Command(SynnefoCommand):
    help = """Reconcile resource usage of Quotaholder with Astakos DB.

    Detect unsynchronized usage between Quotaholder and Astakos DB resources
    and synchronize them if specified so.

    """

    option_list = SynnefoCommand.option_list + (
        make_option("--userid", dest="userid",
                    default=None,
                    help="Reconcile resources only for this user"),
        make_option("--project",
                    help="Reconcile resources only for this project"),
        make_option("--fix", dest="fix",
                    default=False,
                    action="store_true",
                    help="Synchronize Quotaholder with Astakos DB."),
        make_option("--force",
                    default=False,
                    action="store_true",
                    help="Override Quotaholder. Force Quotaholder to impose"
                         " the quota, independently of their value.")
    )

    @transaction.commit_on_success
    def handle(self, *args, **options):
        write = self.stderr.write
        force = options['force']
        userid = options['userid']
        project = options['project']

        resources = [quotas.PENDING_APP_RESOURCE]

        try:
            astakos = Component.objects.get(name="astakos")
        except Component.DoesNotExist:
            raise CommandError("Component 'astakos' not found.")

        query = [userid] if userid is not None else None
        qh_holdings = quotas.service_get_quotas(astakos, query)
        query = [project] if project is not None else None
        qh_project_holdings = quotas.service_get_project_quotas(astakos, query)

        if userid is None:
            users = AstakosUser.objects.accepted().select_related(
                'base_project')
        else:
            try:
                user = AstakosUser.objects.get(uuid=userid)
            except AstakosUser.DoesNotExist:
                raise CommandError("There is no user with uuid '%s'." % userid)
            if not user.is_accepted():
                raise CommandError("%s is not an accepted user." % userid)
            users = [user]

        db_holdings = count_pending_app(users)

        db_project_holdings = {}
        for user, user_holdings in db_holdings.iteritems():
            db_project_holdings.update(user_holdings)

        unsynced_users, users_pending, users_unknown =\
            reconcile.check_users(self.stderr, resources,
                                  db_holdings, qh_holdings)

        unsynced_projects, projects_pending, projects_unknown =\
            reconcile.check_projects(self.stderr, resources,
                                     db_project_holdings, qh_project_holdings)
        pending_exists = users_pending or projects_pending
        unknown_exists = users_unknown or projects_unknown

        headers = ("Type", "Holder", "Source", "Resource",
                   "Astakos", "Quotaholder")
        unsynced = unsynced_users + unsynced_projects
        if unsynced:
            pprint_table(self.stdout, unsynced, headers)
            if options["fix"]:
                user_provisions = create_user_provisions(unsynced_users)
                project_provisions = create_project_provisions(
                    unsynced_projects)
                provisions = user_provisions + project_provisions
                name = ("client: reconcile-resources-astakos, time: %s"
                        % datetime.now())
                try:
                    s = qh.issue_commission('astakos', provisions,
                                            name=name, force=force)
                except qh_exception.NoCapacityError:
                    write("Reconciling failed because a limit has been "
                          "reached. Use --force to ignore the check.\n")
                    return

                qh.resolve_pending_commission('astakos', s)
                write("Fixed unsynced resources\n")

        if pending_exists:
            write("Found pending commissions. "
                  "This is probably a bug. Please report.\n")
        elif not (unsynced or unknown_exists):
            write("Everything in sync.\n")


def create_user_provisions(provision_list):
    provisions = []
    for _, holder, source, resource, db_value, qh_value in provision_list:
        value = db_value - qh_value
        provisions.append(
            quotas.mk_user_provision(holder, source, resource, value))
    return provisions


def create_project_provisions(provision_list):
    provisions = []
    for _, holder, _, resource, db_value, qh_value in provision_list:
        value = db_value - qh_value
        provisions.append(
            quotas.mk_project_provision(holder, resource, value))
    return provisions
