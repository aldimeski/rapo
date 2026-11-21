"""Contains the definition of a workflow: reading and checking it.

A workflow is a list of jobs run one after another, with parameters that
override those of the jobs, a `timeout` and an `overlap` policy. It is one
JSON document: `{"version": 1, "steps": [{"job": "a"}, {"job": "b"}],
"parameters": [], "timeout": null, "overlap": "skip"}`. See
docs/jobs-design.md, section 11.
"""

import re

from . import catalogue
from . import definition as definitions

VERSION = 1
KEYS = {'version', 'steps', 'parameters', 'timeout', 'overlap',
        'condition'}
JOB_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')


class WorkflowError(definitions.DefinitionError):
    """A workflow definition that is not valid; `problems` lists what is
    wrong."""


def normalize(definition):
    """Get the definition with its defaults filled in; `check` is what finds
    the problems. A step given as a job name becomes an object."""
    definition = definitions.load(definition)
    definition.setdefault('version', VERSION)
    definition.setdefault('steps', [])
    definition.setdefault('parameters', [])
    definition.setdefault('timeout', None)
    definition.setdefault('overlap', 'skip')
    definition.setdefault('condition', None)
    if isinstance(definition.get('condition'), dict) and isinstance(
            definition['condition'].get('kind'), str):
        definition['condition']['kind'] = \
            definition['condition']['kind'].strip().upper()
    if isinstance(definition['steps'], list):
        definition['steps'] = [{'job': step} if isinstance(step, str)
                               else step for step in definition['steps']]
    return definition


def job_names(definition):
    """Get the names of the jobs of the steps, in order."""
    return [step.get('job') for step in definition.get('steps') or []
            if isinstance(step, dict)]


def repeated(names):
    """Get {name: [step numbers]} of the jobs that are in more than one
    step; the numbers count from 1."""
    places = {}
    for number, name in enumerate(names, 1):
        places.setdefault(name, []).append(number)
    return {name: numbers for name, numbers in places.items()
            if len(numbers) > 1}


def _steps_text(numbers):
    if len(numbers) == 2:
        return f'steps {numbers[0]} and {numbers[1]}'
    return 'steps ' + ', '.join(str(number) for number in numbers[:-1]) + \
        f' and {numbers[-1]}'


def validate(definition, known_jobs=None, owner=None):
    """Get the list of problems of a normalized definition, empty when it
    can be saved. `known_jobs` is the set of names of the jobs that exist;
    without it the names are not looked up. `owner` is the name of the
    workflow, which a condition may not wait for."""
    problems = []
    unknown = sorted(set(definition) - KEYS)
    if unknown:
        problems.append(f'The workflow has no setting {", ".join(unknown)}.')
    if definition.get('version') != VERSION:
        problems.append(f'version is {VERSION}.')
    steps = definition.get('steps')
    if not isinstance(steps, list):
        problems.append('steps is a list of jobs.')
    elif not steps:
        problems.append('A workflow needs at least one step: add a job.')
    else:
        names = []
        for number, step in enumerate(steps, 1):
            label = f'Step {number}'
            if not isinstance(step, dict):
                problems.append(f'{label}: is not an object.')
                continue
            extra = sorted(set(step) - {'job'})
            if extra:
                problems.append(f'{label}: has no setting {", ".join(extra)}.')
            name = step.get('job')
            if not (isinstance(name, str) and JOB_NAME.match(name)):
                problems.append(f'{label}: job is the name of a job.')
                continue
            names.append(name)
            if known_jobs is not None and name not in known_jobs:
                problems.append(f'{label}: there is no job {name}.')
        for name, numbers in repeated(names).items():
            problems.append(f'The job {name} is repeated, at '
                            f'{_steps_text(numbers)}: a job can be in a '
                            'workflow once.')
    definitions._check_parameters(definition.get('parameters'), problems)
    if definition.get('condition') is not None:
        definitions.check_condition(
            definition['condition'],
            {'owner': ('workflow', owner)} if owner else {}, problems,
            kinds=catalogue.WORKFLOW_CONDITION_KINDS,
            label='Condition of the workflow')
    timeout = definition.get('timeout')
    if timeout is not None and (isinstance(timeout, bool)
                                or not isinstance(timeout, int)
                                or timeout < 0):
        problems.append('timeout is a number of seconds, 0 for none.')
    if definition.get('overlap') not in catalogue.OVERLAPS:
        problems.append(f'overlap is one of {", ".join(catalogue.OVERLAPS)}.')
    return problems


def check(definition, known_jobs=None, owner=None):
    """Get the normalized definition, raising WorkflowError with every
    problem found."""
    definition = normalize(definition)
    problems = validate(definition, known_jobs, owner)
    if problems:
        raise WorkflowError(problems)
    return definition


def remove_job(definition, name):
    """Get the definition without the steps of the job, and whether any
    was there."""
    steps = [step for step in definition.get('steps') or []
             if not (isinstance(step, dict) and step.get('job') == name)]
    found = len(steps) != len(definition.get('steps') or [])
    return dict(definition, steps=steps), found
