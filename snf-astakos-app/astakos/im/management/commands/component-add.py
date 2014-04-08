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
from snf_django.management.commands import SynnefoCommand, CommandError
from astakos.im.models import Component


class Command(SynnefoCommand):
    args = "<name>"
    help = "Register a component"

    option_list = SynnefoCommand.option_list + (
        make_option('--ui-url',
                    dest='ui_url',
                    default=None,
                    help="Set UI URL"),
        make_option('--base-url',
                    dest='base_url',
                    default=None,
                    help="Set base URL"),
    )

    def handle(self, *args, **options):
        if len(args) != 1:
            raise CommandError("Invalid number of arguments")

        name = args[0]
        base_url = options['base_url']
        ui_url = options['ui_url']

        try:
            Component.objects.get(name=name)
            m = "There already exists a component named '%s'." % name
            raise CommandError(m)
        except Component.DoesNotExist:
            pass

        try:
            c = Component.objects.create(
                name=name, url=ui_url, base_url=base_url)
        except BaseException:
            raise CommandError("Failed to register component.")
        else:
            self.stdout.write('Token: %s\n' % c.auth_token)
