#!/usr/bin/env python3
"""Checks the recipes in recipes/*.json and builds docs/feed.json for the Redroid Recipes app.

    python scripts/feed_tool.py check    # validate every recipe, exit 1 on any problem
    python scripts/feed_tool.py build    # check, then write docs/feed.json
    python scripts/feed_tool.py verify   # check, then make sure docs/feed.json is up to date (used by CI)

The app downloads docs/feed.json (served by GitHub Pages). The limits below are also enforced by the app when it reads the
feed, so a recipe that passes here is safe to show.
"""
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPES = os.path.join(ROOT, 'recipes')
DOCS = os.path.join(ROOT, 'docs')
FEED = os.path.join(DOCS, 'feed.json')
BASE_URL = 'https://redroidapps.github.io/recommended_recipes/'

SCHEMA_VERSION = 1
LANGS = ('en', 'ro', 'ar')
CATEGORIES = ('Breakfast', 'Lunch', 'Dinner', 'Dessert', 'Snacks')
BLOCKED_COUNTRIES = {'israel'}          # never allowed in the app
MAX_ITEMS = 30
MAX_FEED_BYTES = 400 * 1024
MAX_IMAGE_BYTES = 300 * 1024
ID_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')


def text_ok(value, limit, label, errors, required=True):
    if value is None or value == '':
        if required:
            errors.append(f'{label}: missing')
        return
    if not isinstance(value, str):
        errors.append(f'{label}: must be text')
        return
    if len(value) > limit:
        errors.append(f'{label}: longer than {limit} characters')
    if '<' in value or '>' in value or 'http://' in value or 'https://' in value:
        errors.append(f'{label}: plain text only (no tags or links)')


def lines_ok(values, max_items, limit, label, errors):
    if not isinstance(values, list) or not values:
        errors.append(f'{label}: needs at least one line')
        return
    if len(values) > max_items:
        errors.append(f'{label}: more than {max_items} lines')
    for i, v in enumerate(values):
        text_ok(v, limit, f'{label}[{i}]', errors)


def check_recipe(path):
    name = os.path.basename(path)
    errors = []
    try:
        with open(path, encoding='utf-8') as f:
            r = json.load(f)
    except Exception as e:  # noqa: BLE001
        return None, [f'{name}: not valid JSON ({e})']
    rid = r.get('id')
    if not isinstance(rid, str) or not ID_RE.match(rid) or len(rid) > 60:
        errors.append('id: lowercase letters, digits and dashes only, up to 60 characters')
    elif rid + '.json' != name:
        errors.append(f'id: must match the file name ({rid}.json)')
    if r.get('category') not in CATEGORIES:
        errors.append('category: one of ' + ', '.join(CATEGORIES))
    country = r.get('country')
    if country is not None:
        text_ok(country, 60, 'country', errors)
        if isinstance(country, str) and country.strip().lower() in BLOCKED_COUNTRIES:
            errors.append('country: not allowed in this app')
    for key, lo, hi in (('prepMinutes', 0, 1440), ('cookMinutes', 0, 1440), ('servings', 1, 100)):
        v = r.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            errors.append(f'{key}: whole number from {lo} to {hi}')
    for field, limit in (('title', 80), ('description', 200)):
        block = r.get(field)
        if not isinstance(block, dict) or 'en' not in block:
            errors.append(f'{field}: needs at least an "en" text')
            continue
        for lang, value in block.items():
            if lang not in LANGS:
                errors.append(f'{field}.{lang}: unknown language (use {", ".join(LANGS)})')
            text_ok(value, limit, f'{field}.{lang}', errors)
    lines_ok(r.get('ingredients'), 60, 200, 'ingredients', errors)
    lines_ok(r.get('steps'), 40, 800, 'steps', errors)
    for lang, block in (r.get('translations') or {}).items():
        if lang not in LANGS or lang == 'en':
            errors.append(f'translations.{lang}: unknown language')
            continue
        if len(block.get('ingredients', r['ingredients'])) != len(r['ingredients']):
            errors.append(f'translations.{lang}.ingredients: needs the same number of lines as the English list')
        if len(block.get('steps', r['steps'])) != len(r['steps']):
            errors.append(f'translations.{lang}.steps: needs the same number of lines as the English steps')
        if 'ingredients' in block:
            lines_ok(block['ingredients'], 60, 200, f'translations.{lang}.ingredients', errors)
        if 'steps' in block:
            lines_ok(block['steps'], 40, 800, f'translations.{lang}.steps', errors)
    image = r.get('image')
    if image is not None:
        p = os.path.join(DOCS, image) if isinstance(image, str) else None
        if not p or not re.match(r'^images/[a-z0-9-]+\.(webp|jpg)$', image):
            errors.append('image: a file in docs/images, named like my-recipe.webp or my-recipe.jpg')
        elif not os.path.isfile(p):
            errors.append(f'image: docs/{image} does not exist')
        elif os.path.getsize(p) > MAX_IMAGE_BYTES:
            errors.append(f'image: larger than {MAX_IMAGE_BYTES // 1024} KB')
    order = r.get('order', 1000)
    if not isinstance(order, int) or isinstance(order, bool):
        errors.append('order: whole number (optional, lower comes first)')
    return r, [f'{name}: {e}' for e in errors]


def load_all():
    files = sorted(f for f in os.listdir(RECIPES) if f.endswith('.json'))
    recipes, problems, seen = [], [], set()
    for f in files:
        r, errs = check_recipe(os.path.join(RECIPES, f))
        problems += errs
        if r is not None and r.get('id') in seen:
            problems.append(f'{f}: duplicate id {r.get("id")}')
        if r is not None:
            seen.add(r.get('id'))
            recipes.append(r)
    if len(recipes) > MAX_ITEMS:
        problems.append(f'feed: more than {MAX_ITEMS} recipes')
    return recipes, problems


def build_feed(recipes, enabled=True):
    items = []
    for r in sorted(recipes, key=lambda x: (x.get('order', 1000), x['id'])):
        item = {k: r[k] for k in ('id', 'category', 'country', 'prepMinutes', 'cookMinutes', 'servings', 'title', 'description', 'ingredients', 'steps')}
        if r.get('translations'):
            item['translations'] = r['translations']
        if r.get('image'):
            item['imageUrl'] = BASE_URL + r['image']
        items.append(item)
    return {'schemaVersion': SCHEMA_VERSION, 'enabled': enabled, 'updatedAt': datetime.date.today().isoformat(), 'items': items}


def dump(feed):
    return json.dumps(feed, ensure_ascii=False, indent=1) + '\n'


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    recipes, problems = load_all()
    if problems:
        print('Problems found:')
        for p in problems:
            print(' -', p)
        return 1
    feed = build_feed(recipes)
    size = len(dump(feed).encode('utf-8'))
    if size > MAX_FEED_BYTES:
        print(f'feed is {size // 1024} KB, limit is {MAX_FEED_BYTES // 1024} KB')
        return 1
    if cmd == 'check':
        print(f'OK: {len(recipes)} recipes, feed would be {size} bytes')
        return 0
    if cmd == 'build':
        os.makedirs(DOCS, exist_ok=True)
        with open(FEED, 'w', encoding='utf-8', newline='\n') as f:
            f.write(dump(feed))
        print(f'Wrote docs/feed.json: {len(recipes)} recipes, {size} bytes')
        return 0
    if cmd == 'verify':
        try:
            with open(FEED, encoding='utf-8') as f:
                current = json.load(f)
        except Exception as e:  # noqa: BLE001
            print('docs/feed.json is missing or invalid:', e)
            return 1
        a = dict(current); b = dict(feed)
        a.pop('updatedAt', None); b.pop('updatedAt', None)
        if a != b:
            print('docs/feed.json is out of date: run "python scripts/feed_tool.py build" and commit it')
            return 1
        print(f'OK: docs/feed.json is up to date ({len(recipes)} recipes)')
        return 0
    print('unknown command', cmd)
    return 2


if __name__ == '__main__':
    sys.exit(main())
