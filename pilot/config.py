"""Validated local settings. Configuration is frozen per run, never inferred from listings."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import tomllib


EFFORTS = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}
LEGACY_ROLES = {
    'reviewer': {'model': 'gpt-5.6-luna', 'reasoning_effort': 'xhigh'},
    'valuator': {'model': 'gpt-5.6-sol', 'reasoning_effort': 'high'},
}


def default_data_dir():
    """Application state belongs to the user, not the installed source tree."""
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'resale-review'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'resale-review'


def table(value, name, keys):
    if not isinstance(value, dict) or set(value) - set(keys):
        raise ValueError(f'{name} must be a table with only: {", ".join(sorted(keys))}')
    return value


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer between {minimum} and {maximum}')
    return value


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f'{name} must be nonempty text without control characters')
    return value.strip()


def economic_policy(value=None):
    value = table(value if value is not None else {}, 'economics',
                  {'minimum_profit_eur', 'sold_max_age_days', 'country', 'currency'})
    if value.get('country', 'DE') != 'DE' or value.get('currency', 'EUR') != 'EUR':
        raise ValueError('Only Germany (DE) and EUR are supported')
    amount = value.get('minimum_profit_eur', '20')
    if isinstance(amount, bool):
        raise ValueError('minimum_profit_eur must be a finite nonnegative amount')
    try:
        amount = Decimal(str(amount))
    except InvalidOperation as exc:
        raise ValueError('minimum_profit_eur must be a finite nonnegative amount') from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal('1000000000') or amount.as_tuple().exponent < -2:
        raise ValueError('minimum_profit_eur must be nonnegative, at most 1 billion, with at most two decimals')
    # Equal amounts must compare identically when a frozen review is matched to
    # a config, regardless of whether TOML used "20", "20.00", or 20.0.
    canonical_amount = format(amount.quantize(Decimal('0.01')), 'f').rstrip('0').rstrip('.')
    if amount == 0:
        canonical_amount = '0'
    return {'country': 'DE', 'currency': 'EUR', 'minimum_profit_eur': canonical_amount,
            'sold_max_age_days': integer(value.get('sold_max_age_days', 180), 'sold_max_age_days', 1, 3650)}


def validate_config(value, base_dir=None):
    value = table(value, 'config', {'schema_version', 'data_dir', 'search', 'roles', 'economics', 'budget', 'capabilities'})
    if type(value.get('schema_version', 1)) is not int or value.get('schema_version', 1) != 1:
        raise ValueError('config schema_version must be 1')
    search = table(value.get('search'), 'search', {'center', 'radius_km', 'categories', 'window_hours'})
    categories = search.get('categories')
    if not isinstance(categories, list) or not categories:
        raise ValueError('search.categories must be a nonempty list')
    categories = [nonempty(c, 'category') for c in categories]
    if len(set(categories)) != len(categories):
        raise ValueError('search.categories must be unique')
    roles = table(value.get('roles'), 'roles', {'reviewer', 'valuator'})
    normalized_roles = {}
    for name in ('reviewer', 'valuator'):
        role = table(roles.get(name), 'roles.' + name, {'model', 'reasoning_effort'})
        model = nonempty(role.get('model'), name + '.model')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model):
            raise ValueError('Model must be a model identifier, not credentials or instructions')
        effort = role.get('reasoning_effort')
        if not isinstance(effort, str) or effort not in EFFORTS:
            raise ValueError('Unsupported reasoning_effort')
        normalized_roles[name] = {'model': model, 'reasoning_effort': effort}
    budget = table(value.get('budget', {}), 'budget', {'max_cases', 'reviewer_job_limit', 'preparation_minutes'})
    max_cases = (integer(budget['max_cases'], 'max_cases', 1, 1000)
                 if budget.get('max_cases') is not None else None)
    reviewer_limit = (integer(budget['reviewer_job_limit'], 'reviewer_job_limit', 1, 1000)
                      if budget.get('reviewer_job_limit') is not None else None)
    capabilities = table(value.get('capabilities', {}), 'capabilities', {'notes'})
    notes = capabilities.get('notes', [])
    if not isinstance(notes, list):
        raise ValueError('capabilities.notes must be a list')
    data_dir = Path(nonempty(value['data_dir'], 'data_dir')).expanduser() if 'data_dir' in value else default_data_dir()
    if not data_dir.is_absolute():
        data_dir = Path(base_dir or Path.cwd()) / data_dir
    return {'schema_version': 1, 'data_dir': str(data_dir.resolve()),
            'search': {'center': nonempty(search.get('center'), 'search.center'),
                       'radius_km': integer(search.get('radius_km'), 'search.radius_km', 1, 1000),
                       'categories': categories,
                       'window_hours': integer(search.get('window_hours', 24), 'search.window_hours', 1, 720)},
            'roles': normalized_roles, 'economics': economic_policy(value.get('economics')),
            'budget': {'max_cases': max_cases, 'reviewer_job_limit': reviewer_limit,
                       'preparation_minutes': integer(budget.get('preparation_minutes', 30), 'preparation_minutes', 1, 10080)},
            'capabilities': {'notes': [nonempty(n, 'capabilities.notes') for n in notes]}}


def load_config(path):
    path = Path(path).resolve()
    with path.open('rb') as handle:
        return validate_config(tomllib.load(handle), base_dir=path.parent)


def role_settings(manifest, role):
    return deepcopy(manifest.get('configuration', {}).get('roles', LEGACY_ROLES)[role])


def example_config():
    """A neutral editable template; model availability is checked by the executor."""
    return '''schema_version = 1
# Each workspace keeps its own private state. Relative paths resolve beside this file.
data_dir = "./private-data"

[search]
center = "Berlin"
radius_km = 50
categories = ["computers"]
window_hours = 24

# Supply identifiers supported by your chosen executor. These are examples.
[roles.reviewer]
model = "gpt-5.6-luna"
reasoning_effort = "xhigh"

[roles.valuator]
model = "gpt-5.6-sol"
reasoning_effort = "high"

[economics]
country = "DE"
currency = "EUR"
minimum_profit_eur = "20"
sold_max_age_days = 180

# Preparation limits do not measure tokens, money or stop external agents.
# One valuation job per selected case remains available after preparation closes.
# Optional count caps are opt-in; absent caps do not limit listing counts.
[budget]
# max_cases = 3
# reviewer_job_limit = 3
preparation_minutes = 30

[capabilities]
notes = []
'''
