# -*- coding: utf-8 -*-
# Copyright 2011-2014 GRNET S.A. All rights reserved.
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

from astakos.im.tests.common import *


NotFound = type('NotFound', (), {})


def find(f, seq):
    for item in seq:
        if f(item):
            return item
    return NotFound


class ProjectAPITest(TestCase):

    def setUp(self):
        self.client = Client()
        component1 = Component.objects.create(name="comp1")
        register.add_service(component1, "σέρβις1", "type1", [])
        # custom service resources
        resource11 = {"name": u"σέρβις1.ρίσορς11",
                      "desc": u"ρίσορς11 desc",
                      "service_type": "type1",
                      "service_origin": u"σέρβις1",
                      "ui_visible": True}
        r, _ = register.add_resource(resource11)
        register.update_base_default(r, 100)
        resource12 = {"name": u"σέρβις1.resource12",
                      "desc": "resource12 desc",
                      "service_type": "type1",
                      "service_origin": u"σέρβις1",
                      "unit": "bytes"}
        r, _ = register.add_resource(resource12)
        register.update_base_default(r, 1024)

        # create user
        self.user1 = get_local_user("test@grnet.gr")
        self.user2 = get_local_user("test2@grnet.gr")
        self.user2.uuid = "uuid2"
        self.user2.save()
        self.user3 = get_local_user("test3@grnet.gr")

        astakos = Component.objects.create(name="astakos")
        register.add_service(astakos, "astakos_account", "account", [])
        # create another service
        pending_app = {"name": "astakos.pending_app",
                       "desc": "pend app desc",
                       "service_type": "account",
                       "service_origin": "astakos_account",
                       "ui_visible": False,
                       "api_visible": False}
        r, _ = register.add_resource(pending_app)
        register.update_base_default(r, 3)
        request = {"resources": {r.name: {"member_capacity": 3,
                                          "project_capacity": 3}}}
        functions.modify_projects_in_bulk(Q(is_base=True), request)

    def create(self, app, headers):
        dump = json.dumps(app)
        r = self.client.post(reverse("api_projects"), dump,
                             content_type="application/json", **headers)
        body = json.loads(r.content)
        return r.status_code, body

    def modify(self, app, project_id, headers):
        dump = json.dumps(app)
        kwargs = {"project_id": project_id}
        r = self.client.put(reverse("api_project", kwargs=kwargs), dump,
                             content_type="application/json", **headers)
        body = json.loads(r.content)
        return r.status_code, body

    def project_action(self, project_id, action, app_id=None, headers=None):
        action_data = {"reason": ""}
        if app_id is not None:
            action_data["app_id"] = app_id
        action = json.dumps({action: action_data})
        r = self.client.post(reverse("api_project_action",
                                     kwargs={"project_id": project_id}),
                             action, content_type="application/json",
                             **headers)
        return r.status_code

    def memb_action(self, memb_id, action, headers):
        action = json.dumps({action: "reason"})
        r = self.client.post(reverse("api_membership_action",
                                     kwargs={"memb_id": memb_id}), action,
                             content_type="application/json", **headers)
        return r.status_code

    def join(self, project_id, headers):
        action = {"join": {"project": project_id}}
        req = json.dumps(action)
        r = self.client.post(reverse("api_memberships"), req,
                             content_type="application/json", **headers)
        body = json.loads(r.content)
        return r.status_code, body

    def enroll(self, project_id, user, headers):
        action = {
            "enroll": {
                "project": project_id,
                "user": user.email,
            }
        }
        req = json.dumps(action)
        r = self.client.post(reverse("api_memberships"), req,
                             content_type="application/json", **headers)
        body = json.loads(r.content)
        return r.status_code, body

    @im_settings(PROJECT_ADMINS=["uuid2"])
    def test_projects(self):
        client = self.client
        h_owner = {"HTTP_X_AUTH_TOKEN": self.user1.auth_token}
        h_admin = {"HTTP_X_AUTH_TOKEN": self.user2.auth_token}
        h_plain = {"HTTP_X_AUTH_TOKEN": self.user3.auth_token}
        r = client.get(reverse("api_project", kwargs={"project_id": 1}))
        self.assertEqual(r.status_code, 401)

        r = client.get(reverse("api_project", kwargs={"project_id": 1}),
                       **h_owner)
        self.assertEqual(r.status_code, 404)
        r = client.get(reverse("api_membership", kwargs={"memb_id": 100}),
                       **h_owner)
        self.assertEqual(r.status_code, 404)

        status = self.memb_action(1, "accept", h_admin)
        self.assertEqual(status, 409)

        app1 = {"name": "test.pr",
                "description": u"δεσκρίπτιον",
                "end_date": "2013-5-5T20:20:20Z",
                "join_policy": "auto",
                "max_members": 5,
                "resources": {u"σέρβις1.ρίσορς11": {
                    "project_capacity": 1024,
                    "member_capacity": 512}}
                }

        status, body = self.modify(app1, 100, h_owner)
        self.assertEqual(status, 404)

        # Create
        status, body = self.create(app1, h_owner)
        self.assertEqual(status, 201)
        project_id = body["id"]
        app_id = body["application"]

        # Get project
        r = client.get(reverse("api_project",
                               kwargs={"project_id": project_id}),
                       **h_owner)
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        self.assertEqual(body["id"], project_id)
        self.assertEqual(body["last_application"]["id"], app_id)
        self.assertEqual(body["last_application"]["state"], "pending")
        self.assertEqual(body["state"], "uninitialized")
        self.assertEqual(body["owner"], self.user1.uuid)
        self.assertEqual(body["description"], u"δεσκρίπτιον")

        # Approve forbidden
        status = self.project_action(project_id, "approve", app_id=app_id,
                                     headers=h_owner)
        self.assertEqual(status, 403)

        # Create another with the same name
        status, body = self.create(app1, h_owner)
        self.assertEqual(status, 201)
        project2_id = body["id"]
        project2_app_id = body["application"]

        # Create yet another, with different name
        app_p3 = copy.deepcopy(app1)
        app_p3["name"] = "new.pr"
        status, body = self.create(app_p3, h_owner)
        self.assertEqual(status, 201)
        project3_id = body["id"]
        project3_app_id = body["application"]

        # No more pending allowed
        status, body = self.create(app_p3, h_owner)
        self.assertEqual(status, 409)

        # Cancel
        status = self.project_action(project3_id, "cancel",
                                     app_id=project3_app_id, headers=h_owner)
        self.assertEqual(status, 200)

        # Get project
        r = client.get(reverse("api_project",
                               kwargs={"project_id": project3_id}),
                       **h_owner)
        body = json.loads(r.content)
        self.assertEqual(body["state"], "deleted")

        # Modify of uninitialized failed
        app2 = {"name": "test.pr",
                "start_date": "2013-5-5T20:20:20Z",
                "end_date": "2013-7-5T20:20:20Z",
                "join_policy": "moderated",
                "leave_policy": "auto",
                "max_members": 3,
                "resources": {u"σέρβις1.ρίσορς11": {
                    "project_capacity": 1024,
                    "member_capacity": 1024}}
                }
        status, body = self.modify(app2, project_id, h_owner)
        self.assertEqual(status, 409)

        # Create the project again
        status, body = self.create(app2, h_owner)
        self.assertEqual(status, 201)
        project_id = body["id"]
        app_id = body["application"]

        # Dismiss failed
        status = self.project_action(project_id, "dismiss", app_id,
                                     headers=h_owner)
        self.assertEqual(status, 409)

        # Deny
        status = self.project_action(project_id, "deny", app_id,
                                     headers=h_admin)
        self.assertEqual(status, 200)

        # Get project
        r = client.get(reverse("api_project",
                               kwargs={"project_id": project_id}),
                       **h_owner)
        body = json.loads(r.content)
        self.assertEqual(body["last_application"]["id"], app_id)
        self.assertEqual(body["last_application"]["state"], "denied")
        self.assertEqual(body["state"], "uninitialized")

        # Dismiss
        status = self.project_action(project_id, "dismiss", app_id,
                                     headers=h_owner)
        self.assertEqual(status, 200)

        # Get project
        r = client.get(reverse("api_project",
                               kwargs={"project_id": project_id}),
                       **h_owner)
        body = json.loads(r.content)
        self.assertEqual(body["last_application"]["id"], app_id)
        self.assertEqual(body["last_application"]["state"], "dismissed")
        self.assertEqual(body["state"], "deleted")

        # Create the project again
        status, body = self.create(app2, h_owner)
        self.assertEqual(status, 201)
        project_id = body["id"]
        app_id = body["application"]

        # Approve
        status = self.project_action(project_id, "approve", app_id,
                                     headers=h_admin)
        self.assertEqual(status, 200)

        # Check memberships
        r = client.get(reverse("api_memberships"), **h_plain)
        body = json.loads(r.content)
        self.assertEqual(len(body), 1)

        # Enroll
        status, body = self.enroll(project_id, self.user3, h_owner)
        self.assertEqual(status, 200)
        m_plain_id = body["id"]

        # Get project
        r = client.get(reverse("api_project",
                               kwargs={"project_id": project_id}),
                       **h_owner)
        body = json.loads(r.content)
        # Join
        status, body = self.join(project_id, h_owner)
        self.assertEqual(status, 200)
        memb_id = body["id"]

        # Check memberships
        r = client.get(reverse("api_memberships"), **h_plain)
        body = json.loads(r.content)
        self.assertEqual(len(body), 2)
        m = find(lambda m: m["project"] == project_id, body)
        self.assertNotEqual(m, NotFound)
        self.assertEqual(m["user"], self.user3.uuid)
        self.assertEqual(m["state"], "accepted")

        r = client.get(reverse("api_memberships"), **h_owner)
        body = json.loads(r.content)
        self.assertEqual(len(body), 3)

        # Check membership
        r = client.get(reverse("api_membership", kwargs={"memb_id": memb_id}),
                       **h_admin)
        m = json.loads(r.content)
        self.assertEqual(m["user"], self.user1.uuid)
        self.assertEqual(m["state"], "requested")
        self.assertEqual(sorted(m["allowed_actions"]),
                         ["accept", "cancel", "reject"])

        r = client.get(reverse("api_membership", kwargs={"memb_id": memb_id}),
                       **h_plain)
        self.assertEqual(r.status_code, 403)

        status = self.memb_action(memb_id, "leave", h_admin)
        self.assertEqual(status, 409)

        status = self.memb_action(memb_id, "cancel", h_owner)
        self.assertEqual(status, 200)

        status, body = self.join(project_id, h_owner)
        self.assertEqual(status, 200)
        self.assertEqual(memb_id, body["id"])

        status = self.memb_action(memb_id, "reject", h_owner)
        self.assertEqual(status, 200)

        status, body = self.join(project_id, h_owner)
        self.assertEqual(status, 200)
        self.assertEqual(memb_id, body["id"])

        status = self.memb_action(memb_id, "accept", h_owner)
        self.assertEqual(status, 200)

        # Enroll fails, already in
        status, body = self.enroll(project_id, self.user1, h_owner)
        self.assertEqual(status, 409)

        # Remove member
        status = self.memb_action(memb_id, "remove", h_owner)
        self.assertEqual(status, 200)

        # Enroll a removed member
        status, body = self.enroll(project_id, self.user1, h_owner)
        self.assertEqual(status, 200)

        # Remove member
        status = self.memb_action(memb_id, "remove", h_owner)
        self.assertEqual(status, 200)

        # Re-join
        status, body = self.join(project_id, h_owner)
        self.assertEqual(status, 200)
        self.assertEqual(memb_id, body["id"])

        # Enroll a requested member
        status, body = self.enroll(project_id, self.user1, h_owner)
        self.assertEqual(status, 200)

        # Enroll fails, already in
        status, body = self.enroll(project_id, self.user1, h_owner)
        self.assertEqual(status, 409)

        # Enroll fails, project does not exist
        status, body = self.enroll(-1, self.user1, h_owner)
        self.assertEqual(status, 409)

        # Get projects
        ## Simple user mode
        r = client.get(reverse("api_projects"), **h_plain)
        body = json.loads(r.content)
        self.assertEqual(len(body), 2)
        p = body[0]
        with assertRaises(KeyError):
            p["pending_application"]

        ## Owner mode
        filters = {"state": "active"}
        r = client.get(reverse("api_projects"), filters, **h_owner)
        body = json.loads(r.content)
        self.assertEqual(len(body), 2)

        filters = {"state": "deleted"}
        r = client.get(reverse("api_projects"), filters, **h_owner)
        body = json.loads(r.content)
        self.assertEqual(len(body), 2)

        filters = {"state": "uninitialized"}
        r = client.get(reverse("api_projects"), filters, **h_owner)
        body = json.loads(r.content)
        self.assertEqual(len(body), 2)

        filters = {"name": "test.pr"}
        r = client.get(reverse("api_projects"), filters, **h_owner)
        body = json.loads(r.content)
        self.assertEqual(len(body), 4)

        filters = {"mode": "member"}
        r = client.get(reverse("api_projects"), filters, **h_owner)
        body = json.loads(r.content)
        self.assertEqual(len(body), 2)

        # Leave failed
        status = self.memb_action(m_plain_id, "leave", h_owner)
        self.assertEqual(status, 403)

        # Leave
        status = self.memb_action(m_plain_id, "leave", h_plain)
        self.assertEqual(status, 200)

        # Suspend failed
        status = self.project_action(project_id, "suspend", headers=h_owner)
        self.assertEqual(status, 403)

        # Unsuspend failed
        status = self.project_action(project_id, "unsuspend", headers=h_admin)
        self.assertEqual(status, 409)

        # Suspend
        status = self.project_action(project_id, "suspend", headers=h_admin)
        self.assertEqual(status, 200)

        # Cannot view project
        r = client.get(reverse("api_project",
                               kwargs={"project_id": project_id}), **h_plain)
        self.assertEqual(r.status_code, 403)

        # Unsuspend
        status = self.project_action(project_id, "unsuspend", headers=h_admin)
        self.assertEqual(status, 200)

        # Cannot approve, project with same name exists
        status = self.project_action(project2_id, "approve", project2_app_id,
                                     headers=h_admin)
        self.assertEqual(status, 409)

        # Terminate
        status = self.project_action(project_id, "terminate", headers=h_admin)
        self.assertEqual(status, 200)

        # Join failed
        status, _ = self.join(project_id, h_admin)
        self.assertEqual(status, 409)

        # Can approve now
        status = self.project_action(project2_id, "approve", project2_app_id,
                                     headers=h_admin)
        self.assertEqual(status, 200)

        # Join new project
        status, body = self.join(project2_id, h_plain)
        self.assertEqual(status, 200)
        m_project2 = body["id"]

        # Get memberships of project
        filters = {"project": project2_id}
        r = client.get(reverse("api_memberships"), filters, **h_owner)
        body = json.loads(r.content)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], m_project2)

        # Remove member
        status = self.memb_action(m_project2, "remove", h_owner)
        self.assertEqual(status, 200)

        # Reinstate failed
        status = self.project_action(project_id, "reinstate", headers=h_admin)
        self.assertEqual(status, 409)

        # Rename
        app2_renamed = copy.deepcopy(app2)
        app2_renamed["name"] = "new.name"
        status, body = self.modify(app2_renamed, project_id, h_owner)
        self.assertEqual(status, 201)
        app2_renamed_id = body["application"]

        # Get project
        r = client.get(reverse("api_project",
                               kwargs={"project_id": project_id}), **h_owner)
        body = json.loads(r.content)
        self.assertEqual(body["last_application"]["id"], app2_renamed_id)
        self.assertEqual(body["state"], "terminated")
        assertIn("deactivation_date", body)
        self.assertEqual(body["last_application"]["state"], "pending")
        self.assertEqual(body["last_application"]["name"], "new.name")
        status = self.project_action(project_id, "approve", app2_renamed_id,
                                     headers=h_admin)
        self.assertEqual(r.status_code, 200)

        # Change homepage
        status, body = self.modify({"homepage": "new.page"},
                                   project_id, h_owner)
        self.assertEqual(status, 201)

        r = client.get(reverse("api_project",
                               kwargs={"project_id": project_id}), **h_owner)
        body = json.loads(r.content)
        self.assertEqual(body["homepage"], "")
        self.assertEqual(body["last_application"]["homepage"], "new.page")
        homepage_app = body["last_application"]["id"]
        status = self.project_action(project_id, "approve", homepage_app,
                                     headers=h_admin)
        self.assertEqual(r.status_code, 200)
        r = client.get(reverse("api_project",
                               kwargs={"project_id": project_id}), **h_owner)
        body = json.loads(r.content)
        self.assertEqual(body["homepage"], "new.page")

        # Bad requests
        r = client.head(reverse("api_projects"), **h_admin)
        self.assertEqual(r.status_code, 405)
        self.assertTrue('Allow' in r)

        r = client.head(reverse("api_project",
                                kwargs={"project_id": 1}), **h_admin)
        self.assertEqual(r.status_code, 405)
        self.assertTrue('Allow' in r)

        r = client.head(reverse("api_memberships"), **h_admin)
        self.assertEqual(r.status_code, 405)
        self.assertTrue('Allow' in r)

        status = self.project_action(1, "nonex", headers=h_owner)
        self.assertEqual(status, 400)

        action = json.dumps({"suspend": "", "unsuspend": ""})
        r = client.post(reverse("api_project_action",
                                kwargs={"project_id": 1}),
                        action, content_type="application/json", **h_owner)
        self.assertEqual(r.status_code, 400)

        ap = {"owner": "nonex",
              "join_policy": "nonex",
              "leave_policy": "nonex",
              "start_date": "nonex",
              "homepage": {},
              "max_members": -3,
              "resources": [],
              }

        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)
        self.assertEqual(body["badRequest"]["message"], "User does not exist.")

        ap["owner"] = self.user1.uuid
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["name"] = "some.name"
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["join_policy"] = "auto"
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["leave_policy"] = "closed"
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["start_date"] = "2013-01-01T0:0Z"
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["end_date"] = "2014-01-01T0:0Z"
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["max_members"] = 0
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["homepage"] = "a.stri.ng"
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["resources"] = {42: 42}
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["resources"] = {u"σέρβις1.ρίσορς11": {
                "member_capacity": 512}}
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["resources"] = {u"σέρβις1.ρίσορς11": {"member_capacity": 512,
                                                 "project_capacity": 1024}}
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 201)

        ap["name"] = "non_domain_name"
        status, body = self.create(ap, h_owner)
        self.assertEqual(status, 400)

        ap["name"] = "domain.name"

        filters = {"state": "nonex"}
        r = client.get(reverse("api_projects"), filters, **h_owner)
        self.assertEqual(r.status_code, 400)

        # directly modify a base project
        with assertRaises(functions.ProjectBadRequest):
            functions.modify_project(self.user1.uuid,
                                     {"description": "new description",
                                      "member_join_policy":
                                          functions.MODERATED_POLICY})
        functions.modify_project(self.user1.uuid,
                                 {"member_join_policy":
                                      functions.MODERATED_POLICY})
        r = client.get(reverse("api_project",
                               kwargs={"project_id": self.user1.uuid}),
                       **h_owner)
        body = json.loads(r.content)
        self.assertEqual(body["join_policy"], "moderated")

        r = self.client.post(reverse("api_projects"), "\xff",
                             content_type="application/json", **h_owner)
        self.assertEqual(r.status_code, 400)

        r = self.client.post(reverse("api_project_action",
                                     kwargs={"project_id": "1234"}),
                             "\"nondict\"", content_type="application/json",
                             **h_owner)
        self.assertEqual(r.status_code, 400)

        r = client.get(reverse("api_project",
                               kwargs={"project_id": u"πρότζεκτ"}),
                       **h_owner)
        self.assertEqual(r.status_code, 404)


class TestProjects(TestCase):
    """
    Test projects.
    """
    def setUp(self):
        # astakos resources
        self.resource = Resource.objects.create(name="astakos.pending_app",
                                                uplimit=0,
                                                project_default=0,
                                                ui_visible=False,
                                                api_visible=False,
                                                service_type="astakos")

        # custom service resources
        self.resource = Resource.objects.create(name="service1.resource",
                                                uplimit=100,
                                                project_default=0,
                                                service_type="service1")
        self.admin = get_local_user("projects-admin@synnefo.org")
        self.admin.uuid = 'uuid1'
        self.admin.save()

        self.user = get_local_user("user@synnefo.org")
        self.member = get_local_user("member@synnefo.org")
        self.member2 = get_local_user("member2@synnefo.org")

        self.admin_client = get_user_client("projects-admin@synnefo.org")
        self.user_client = get_user_client("user@synnefo.org")
        self.member_client = get_user_client("member@synnefo.org")
        self.member2_client = get_user_client("member2@synnefo.org")

    def tearDown(self):
        Service.objects.all().delete()
        ProjectApplication.objects.all().delete()
        Project.objects.all().delete()
        AstakosUser.objects.all().delete()

    @im_settings(PROJECT_ADMINS=['uuid1'])
    def test_application_limit(self):
        # user cannot create a project
        r = self.user_client.get(reverse('project_add'), follow=True)
        self.assertRedirects(r, reverse('project_list'))
        self.assertContains(r, "You are not allowed to create a new project")

        # but admin can
        r = self.admin_client.get(reverse('project_add'), follow=True)
        self.assertRedirects(r, reverse('project_add'))

    @im_settings(PROJECT_ADMINS=['uuid1'])
    def test_ui_visible(self):
        dfrom = datetime.now()
        dto = datetime.now() + timedelta(days=30)

        # astakos.pending_app ui_visible flag is False
        # we shouldn't be able to create a project application using this
        # resource.
        application_data = {
            'name': 'project.synnefo.org',
            'homepage': 'https://www.synnefo.org',
            'start_date': dfrom.strftime("%Y-%m-%d"),
            'end_date': dto.strftime("%Y-%m-%d"),
            'member_join_policy': 2,
            'member_leave_policy': 1,
            'limit_on_members_number': 5,
            'service1.resource_m_uplimit': 100,
            'is_selected_service1.resource': "1",
            'astakos.pending_app_m_uplimit': 100,
            'is_selected_accounts': "1",
            'user': self.user.pk
        }
        form = forms.ProjectApplicationForm(data=application_data)
        # form is invalid
        self.assertEqual(form.is_valid(), False)

        del application_data['astakos.pending_app_m_uplimit']
        del application_data['is_selected_accounts']
        form = forms.ProjectApplicationForm(data=application_data)
        self.assertEqual(form.is_valid(), True)

    @im_settings(PROJECT_ADMINS=['uuid1'])
    def no_test_applications(self):
        # let user have 2 pending applications

        # TODO figure this out
        request = {"resources": {"astakos.pending_app":
                                     {"member_capacity": 2,
                                      "project_capacity": 2}}}
        functions.modify_project(self.user.uuid, request)

        r = self.user_client.get(reverse('project_add'), follow=True)
        self.assertRedirects(r, reverse('project_add'))

        # user fills the project application form
        post_url = reverse('project_add') + '?verify=1'
        dfrom = datetime.now()
        dto = datetime.now() + timedelta(days=30)
        application_data = {
            'name': 'project.synnefo.org',
            'homepage': 'https://www.synnefo.org',
            'start_date': dfrom.strftime("%Y-%m-%d"),
            'end_date': dto.strftime("%Y-%m-%d"),
            'member_join_policy': 2,
            'member_leave_policy': 1,
            'service1.resource_m_uplimit': 100,
            'is_selected_service1.resource': "1",
            'user': self.user.pk
        }
        r = self.user_client.post(post_url, data=application_data, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['form'].is_valid(), False)

        application_data['limit_on_members_number'] = 5
        r = self.user_client.post(post_url, data=application_data, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['form'].is_valid(), True)

        # confirm request
        post_url = reverse('project_add') + '?verify=0&edit=0'
        r = self.user_client.post(post_url, data=application_data, follow=True)
        self.assertContains(r, "The project application has been received")
        self.assertRedirects(r, reverse('project_list'))
        self.assertEqual(ProjectApplication.objects.count(), 1)
        app1 = ProjectApplication.objects.filter().order_by('pk')[0]
        app1_id = app1.pk
        project1_id = app1.chain_id

        # create another one
        application_data['name'] = 'project2.synnefo.org'
        r = self.user_client.post(post_url, data=application_data, follow=True)
        app2 = ProjectApplication.objects.filter().order_by('pk')[1]
        project2_id = app2.chain_id

        # no more applications (LIMIT is 2)
        r = self.user_client.get(reverse('project_add'), follow=True)
        self.assertRedirects(r, reverse('project_list'))
        self.assertContains(r, "You are not allowed to create a new project")

        # one project per application
        self.assertEqual(Project.objects.filter(is_base=False).count(), 2)

        # login
        self.admin_client.get(reverse("edit_profile"))
        # admin approves
        r = self.admin_client.post(reverse('project_app_approve',
                                           kwargs={'application_id': app1_id}),
                                   follow=True)
        self.assertEqual(r.status_code, 200)

        Q_ACTIVE = Project.o_state_q(Project.O_ACTIVE)
        self.assertEqual(Project.objects.filter(Q_ACTIVE).count(), 1)

        # login
        self.member_client.get(reverse("edit_profile"))
        # cannot join project2 (not approved yet)
        join_url = reverse("project_join", kwargs={'chain_id': project2_id})
        r = self.member_client.post(join_url, follow=True)

        # can join project1
        self.member_client.get(reverse("edit_profile"))
        join_url = reverse("project_join", kwargs={'chain_id': project1_id})
        r = self.member_client.post(join_url, follow=True)
        self.assertEqual(r.status_code, 200)

        memberships = ProjectMembership.objects.all()
        self.assertEqual(len(memberships), 1)
        memb_id = memberships[0].id

        reject_member_url = reverse('project_reject_member',
                                    kwargs={'memb_id': memb_id})
        accept_member_url = reverse('project_accept_member',
                                    kwargs={'memb_id': memb_id})

        # only project owner is allowed to reject
        r = self.member_client.post(reject_member_url, follow=True)
        self.assertContains(r, "You do not have the permissions")
        self.assertEqual(r.status_code, 200)

        # user (owns project) rejects membership
        r = self.user_client.post(reject_member_url, follow=True)
        self.assertEqual(ProjectMembership.objects.any_accepted().count(), 0)

        # user rejoins
        self.member_client.get(reverse("edit_profile"))
        join_url = reverse("project_join", kwargs={'chain_id': project1_id})
        r = self.member_client.post(join_url, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ProjectMembership.objects.requested().count(), 1)

        # user (owns project) accepts membership
        r = self.user_client.post(accept_member_url, follow=True)
        self.assertEqual(ProjectMembership.objects.any_accepted().count(), 1)
        membership = ProjectMembership.objects.get()
        self.assertEqual(membership.state, ProjectMembership.ACCEPTED)

        user_quotas = quotas.get_users_quotas([self.member])
        resource = 'service1.resource'
        newlimit = user_quotas[self.member.uuid]['system'][resource]['limit']
        # 100 from initial uplimit + 100 from project
        self.assertEqual(newlimit, 200)

        remove_member_url = reverse('project_remove_member',
                                    kwargs={'memb_id': membership.id})
        r = self.user_client.post(remove_member_url, follow=True)
        self.assertEqual(r.status_code, 200)

        user_quotas = quotas.get_users_quotas([self.member])
        resource = 'service1.resource'
        newlimit = user_quotas[self.member.uuid]['system'][resource]['limit']
        # 200 - 100 from project
        self.assertEqual(newlimit, 100)

        # support email gets rendered in emails content
        for mail in get_mailbox('user@synnefo.org'):
            self.assertTrue(settings.CONTACT_EMAIL in
                            mail.message().as_string())
