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

from optparse import make_option

from snf_django.management.commands import SynnefoCommand, CommandError

from snf_django.management.utils import parse_bool
from synnefo.management import common, pprint
from synnefo.volume import volumes

HELP_MSG = """Create a new volume."""


class Command(SynnefoCommand):
    help = HELP_MSG

    option_list = SynnefoCommand.option_list + (
        make_option(
            "--name",
            dest="name",
            default=None,
            help="Display name of the volume."),
        make_option(
            "--description",
            dest="description",
            default=None,
            help="Display description of the volume."),
        make_option(
            "--owner",
            dest="user_id",
            default=None,
            help="UUID of the owner of the volume."),
        make_option(
            "--project",
            dest="project",
            default=None,
            help="UUID of the project of the volume."),
        make_option(
            "-s", "--size",
            dest="size",
            default=None,
            help="Size of the new volume in GB"),
        make_option(
            "--source",
            dest="source",
            default=None,
            help="Initialize volume with data from the specified source. The"
                 " source must be of the form <source_type>:<source_uuid>."
                 " Available source types are 'image', 'snapshot' and"
                 " 'volume'."),
        make_option(
            "--server",
            dest="server_id",
            default=None,
            help="The ID of the server that the volume will be connected to."),
        make_option(
            "--wait",
            dest="wait",
            default="True",
            choices=["True", "False"],
            metavar="True|False",
            help="Wait for Ganeti jobs to complete."),
    )

    @common.convert_api_faults
    def handle(self, *args, **options):
        if args:
            raise CommandError("Command doesn't accept any arguments")

        size = options.get("size")
        user_id = options.get("user_id")
        project_id = options.get("project")
        server_id = options.get("server_id")
        wait = parse_bool(options["wait"])

        display_name = options.get("name", "")
        display_description = options.get("description", "")

        if size is None:
            raise CommandError("Please specify the size of the volume")

        if server_id is None:
            raise CommandError("Please specify the server to attach the"
                               " volume.")

        vm = common.get_resource("server", server_id, for_update=True)

        if user_id is None:
            user_id = vm.userid

        if project_id is None:
            project_id = vm.project

        source_image_id = source_volume_id = source_snapshot_id = None
        source = options.get("source")
        if source is not None:
            try:
                source_type, source_uuid = source.split(":", 1)
            except (ValueError, TypeError):
                raise CommandError("Invalid '--source' option. Value must be"
                                   " of the form <source_type>:<source_uuid>")
            if source_type == "image":
                source_image_id = source_uuid
            elif source_type == "snapshot":
                source_snapshot_id = source_uuid
            elif source_type == "volume":
                source_volume_id = source_uuid
            else:
                raise CommandError("Unknown volume source type '%s'"
                                   % source_type)

        volume = volumes.create(user_id, size, server_id,
                                name=display_name,
                                description=display_description,
                                source_image_id=source_image_id,
                                source_snapshot_id=source_snapshot_id,
                                source_volume_id=source_volume_id,
                                metadata={}, project=project_id)

        self.stdout.write("Created volume '%s' in DB:\n" % volume.id)

        pprint.pprint_volume(volume, stdout=self.stdout)
        self.stdout.write("\n")
        if volume.machine is not None:
            volume.machine.task_job_id = volume.backendjobid
            common.wait_server_task(volume.machine, wait, stdout=self.stdout)
