"""Tests of the access scopes of the API (rapo/web/api/scopes.py). Run from
the project root:

    python -m unittest tests.test_scopes -v

`scopes.py` needs nothing of rapo, so it is loaded by its path; the routes
of `app.py` are read from its text, as the whole of rapo needs Oracle.
"""
import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, 'rapo', 'web', 'api')

spec = importlib.util.spec_from_file_location(
    'scopes_under_test', os.path.join(API, 'scopes.py'))
scopes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scopes)


def app_routes():
    """Get (method, route name) of every route of app.py."""
    with open(os.path.join(API, 'app.py'), encoding='utf-8') as file:
        text = file.read()
    return re.findall(r"@api\.(get|post|delete|put)\(\s*'/([a-z0-9-]+)'",
                      text)


class ScopesTests(unittest.TestCase):
    def test_every_route_of_the_api_has_a_scope(self):
        names = {name for _, name in app_routes()}
        self.assertGreater(len(names), 100)
        missing = names - scopes.PUBLIC - scopes.MASTER - set(scopes.ROUTES)
        self.assertEqual(sorted(missing), [])

    def test_every_scope_names_a_route_that_exists(self):
        names = {name for _, name in app_routes()}
        stale = set(scopes.ROUTES) - names
        self.assertEqual(sorted(stale), [])

    def test_every_route_needs_a_level_its_area_offers(self):
        for name, (area, level) in scopes.ROUTES.items():
            self.assertIn(area, scopes.AREAS, msg=name)
            self.assertIn(level, scopes.AREAS[area][1], msg=name)
        for name, rules in scopes.ALSO.items():
            for area, level in rules:
                self.assertIn(level, scopes.AREAS[area][1], msg=name)

    def test_every_level_of_every_area_is_described(self):
        for area, (_, levels) in scopes.AREAS.items():
            for level in levels:
                if level != 'none':
                    self.assertIn(level, scopes.DESCRIPTIONS[area],
                                  msg=f'{area}: {level}')

    def test_the_presets_are_valid_tokens(self):
        for name, preset in scopes.PRESETS.items():
            scopes.normalize(preset['scopes'], preset['free_sql'])

    def test_jobs_are_an_area_with_four_levels(self):
        self.assertEqual(scopes.AREAS['jobs'][1],
                         ('none', 'view', 'operate', 'edit'))

    def test_the_levels_of_the_jobs_in_the_presets(self):
        def level(name):
            return scopes.normalize(scopes.PRESETS[name]['scopes'],
                                    scopes.PRESETS[name]['free_sql'])['jobs']
        self.assertEqual(level('viewer'), 'view')
        self.assertEqual(level('operator'), 'operate')
        # Editing jobs, which run programs, is for the full token only.
        self.assertEqual(level('editor'), 'none')
        self.assertEqual(level('full'), 'edit')

    def test_what_a_level_of_the_jobs_may_do(self):
        def needs(name):
            return scopes.ROUTES[name]
        for name in ('get-jobs', 'get-job', 'get-job-runs', 'get-job-run',
                     'get-job-versions', 'job-scheduler-status',
                     'job-scheduler-upcoming', 'job-scheduler-events',
                     'get-job-catalogue'):
            self.assertEqual(needs(name), ('jobs', 'view'), msg=name)
        for name in ('run-job', 'cancel-job-run', 'set-job-status',
                     'job-scheduler-start', 'job-scheduler-stop'):
            self.assertEqual(needs(name), ('jobs', 'operate'), msg=name)
        for name in ('save-job', 'delete-job', 'save-job-schedule',
                     'check-job'):
            self.assertEqual(needs(name), ('jobs', 'edit'), msg=name)

    def test_a_token_reaches_the_levels_below_its_own(self):
        held = {'jobs': 'operate'}
        self.assertTrue(scopes.allows(held, 'jobs', 'view'))
        self.assertTrue(scopes.allows(held, 'jobs', 'operate'))
        self.assertFalse(scopes.allows(held, 'jobs', 'edit'))
        self.assertFalse(scopes.allows({}, 'jobs', 'view'))

    def test_a_token_of_an_older_version_has_no_jobs(self):
        # A token stored before the area existed holds no level for it.
        self.assertFalse(scopes.allows({'controls': 'edit'}, 'jobs', 'view'))


if __name__ == '__main__':
    unittest.main()
