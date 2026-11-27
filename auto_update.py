"""Stage, validate and promote a complete SFR update. Publication is done by Actions."""
import json
import re
import shutil
import tempfile
from collections import defaultdict
from contextlib import contextmanager, ExitStack
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

import build_site
from catalog import (derive, document_filename, document_identity, export, individual_documents, individual_number,
                     named_binding_rows, preserves_wsdl_binding, schema_qname, update_predecessors)
from collector_snapshots import (applicable_bindings, collector_source, confirmed_binding_index,
                                 replay_context)
from document_metadata import numbered_volume
from fields import build
from edition_groups import display_updates, group_editions, source_updates
from state_lock import catalog_lock_root, file_lock
from sync import catalog_resource, encode, resource_url, sync, write


def validate_publications(previous, current):
    revoked = {row['revision_id'] for row in current.get('revoked_revisions', [])}
    for env in ('prod', 'test'):
        for category in ('revisions', 'registry'):
            def listed(catalog, ignored=()):
                return {row['number'] for row in catalog.get(category, [])
                        if row['environment'] == env and row.get('listed_now', True)
                        and row.get('source_type') != 'manual' and row.get('revision_id') not in ignored}
            old, new = listed(previous, revoked if category == 'revisions' else ()), listed(current)
            removed = old - new
            # A single retirement is normal. Losing over 20% of a contour's
            # publications requires investigation before promoting the snapshot.
            if old and ((category == 'revisions' and not new) or len(removed) > max(1, len(old) * 0.2)):
                raise ValueError(f'Массовое исчезновение публикаций {env}/{category}: '
                                 f'{len(old)} -> {len(new)}, исчезло {len(removed)}; '
                                 + ', '.join(sorted(removed)[:10]))


def validate_published_trees(previous, current, *, state, candidate_state):
    """Keep a ready tree for an unchanged DOCX matched to the confirmed WSDL."""
    if previous == current:
        return
    publications = {(row['environment'], row['url'], row['sha256']) for row in current['entries']
                    if row['kind'] == 'docx' and row.get('sha256')}
    documents = list(individual_documents(current))
    old_resolutions, resolutions = {}, {}
    old_rows = [row for row in derive(previous, resolutions=old_resolutions)['revisions']
                if row['status'] == 'matched']
    derived = derive(current, resolutions=resolutions)
    failures = {(document_identity(row), row['document_url']): row for row in derived.get('document_sources', [])}
    new_rows = {}
    for row in derived['revisions']:
        if row['status'] == 'matched':
            key = document_identity(row), row['document_url']
            replacement = resolutions.get(key, row)
            new_rows.setdefault(key, []).append(replacement)

    identities = {identity for identity, _ in documents}
    active, confirmed, observations = replay_context(state)
    _, candidate_snapshots, _ = replay_context(candidate_state)
    # New historical replays use the confirmed DOCX timeline. Their proof
    # applies to this installation without treating the new pointer as prior.
    confirmed.update({key: snapshot for key, snapshot in candidate_snapshots.items()
                      if 'collector_observation' in snapshot})
    bindings = applicable_bindings(confirmed_binding_index(active, confirmed, observations), identities)
    prior = {document_identity(row) for row in old_rows}
    # The last confirmed live binding takes precedence when the active
    # inventory itself proves the named member.
    bindings.update({document_identity(row): row for row in named_binding_rows(previous, old_resolutions)})
    errors = []
    for identity, document in documents:
        binding = bindings.get(identity)
        url = document['url']
        replacements = new_rows.get((identity, url), [])
        if binding and (identity in prior or replacements) and not any(
                preserves_wsdl_binding(binding, row) for row in replacements):
            locator = binding['schema_docx_location']
            failure = failures.get((identity, url), {})
            reason = failure.get('schema_reason') or f'Утрачена подтверждённая привязка DOCX к XSD: {locator}.'
            errors.append(f'{identity[0]}/{identity[1]} {url} — {reason}')

    # Keep the former missing-tree guard for WSDL rows without a named proof.
    for row in old_rows:
        identity = document_identity(row)
        environment, number, digest = identity
        url = row['document_url']
        if (environment, url, digest) not in publications or (identity, url) in new_rows:
            continue
        failure = failures.get((identity, url), {})
        if row.get('schema_source_type') == 'wsdl' or failure.get('schema_docx_location'):
            reason = failure.get('schema_reason', 'Полная XSD больше не сопоставлена.')
            errors.append(f'{environment}/{number} {url} — {reason}')
    if errors:
        raise ValueError('Опубликованные документы потеряли готовое дерево XSD: '
                         + '; '.join(sorted(set(errors))))


def inventory_resources(snapshot, env):
    urls, volumes = set(), defaultdict(set)
    for row in snapshot['entries']:
        url = row['url']
        if (row['environment'] != env or row.get('source_type') == 'wsdl' or
                row['kind'] == 'wsdl_status' or not url.startswith(('http://', 'https://'))):
            continue
        resource = resource_url(url)
        urls.add(resource)
        if (row['kind'] == 'docx' and row.get('sha256') and not row.get('error') and
                urlsplit(url).path.startswith('/specs_sedo/') and catalog_resource(url, env)):
            volume = numbered_volume(document_filename(row))
            if volume and volume[2] == individual_number(row):
                volumes[volume].add(resource)
    return urls, volumes


def inventory_changes(previous, current):
    changes = {}
    for env in ('prod', 'test'):
        old, old_volumes = inventory_resources(previous, env)
        new, new_volumes = inventory_resources(current, env)
        removed, added = old - new, new - old
        replacements = []
        for volume, old_urls in sorted(old_volumes.items()):
            new_urls = new_volumes.get(volume, set())
            if len(old_urls) == len(new_urls) == 1 and old_urls <= removed and new_urls <= added:
                replacements.append(dict(previous_url=next(iter(old_urls)), current_url=next(iter(new_urls))))
        unexplained = sorted(removed - {pair['previous_url'] for pair in replacements})
        changes[env] = dict(previous_count=len(old), current_count=len(new),
                            added=sorted(added), removed=sorted(removed),
                            unexplained_removed=unexplained, replacements=replacements)
    return changes


def validate_inventory(previous, current, *, diagnostics=None):
    """Protect each contour against mass loss while allowing verified volume replacements."""
    changes = inventory_changes(previous, current)
    if diagnostics is not None:
        diagnostics.update(changes)
    errors = []
    for env, delta in changes.items():
        old, new = delta['previous_count'], delta['current_count']
        unexplained = delta['unexplained_removed']
        if len(unexplained) > max(1, old * 0.2):
            errors.append(f'Массовое исчезновение ссылок {env}: {old} -> {new}, '
                          f'исчезло без замены {len(unexplained)}; ' + ', '.join(unexplained[:10]))
    if errors:
        raise ValueError('; '.join(errors))


def active_inventory(state):
    key = json.loads((state / 'current.json').read_bytes())['snapshot']
    return json.loads((state / 'snapshots' / (key + '.json')).read_bytes())


def archived_restoration(state, rejection, owners):
    """Recover a legacy rejection's immutable source from its own snapshot and objects."""
    from catalog import archived_graph_reader
    from document_metadata import read_document_display
    from edition_groups import SOURCE_KEYS
    from sync import sha

    snapshot, rid = rejection['snapshot'], rejection['revision_id']
    raw = (state / 'snapshots' / (snapshot + '.json')).read_bytes()
    if sha(raw) != snapshot:
        raise ValueError('Изменился исходный снимок восстановления')
    stored = json.loads(raw)
    owner = owners.get(rid, {})
    rows = derive(stored, owners, archive_graph=archived_graph_reader(state))['revisions']
    matches = [r for r in rows if r['revision_id'] == rid and
               all(r.get(k) == rejection.get(k) for k in ('environment', 'number', 'document_url', 'document_sha256'))]
    if len(matches) != 1:
        raise ValueError('Исходный снимок не подтверждает восстановленную привязку')
    row = matches[0]
    if (not row.get('resolved_schema_graph') or row['resolved_schema_graph'] != owner.get('resolved_schema_graph')
            or schema_qname(row) != schema_qname(owner)):
        raise ValueError('Изменился владелец восстановленной привязки')
    document = (state / 'objects' / row['document_sha256']).read_bytes()
    if sha(document) != row['document_sha256']:
        raise ValueError('Изменился исходный DOCX восстановленной привязки')
    source = {k: row[k] for k in SOURCE_KEYS if k in row and k not in ('listed_now', 'document_download', 'document_filename')}
    original = next((doc for doc in stored['entries'] if doc.get('sha256') == row['document_sha256']
                     and doc.get('url') == row['document_url'] and doc['environment'] == row['environment']), {})
    if 'document_history' not in original:
        source['document_history'] = read_document_display(document, legacy=True)['document_history']
    if stored.get('format', 1) == 1 and row.get('source_type') == 'module':
        membership = next((r for r in derive(stored)['registry'] if (r['environment'], r['number']) ==
                           (row['environment'], row['number'])), None)
        if membership:
            source['module'] = membership['module']
    stamps = json.loads((state / 'observations.json').read_bytes())['snapshots']
    history = json.loads((state / 'history.json').read_bytes())
    return dict(snapshot=snapshot, revision_id=rid, observed_at=stamps.get(snapshot),
                initial_import=history[0] == snapshot, source=source)


def validate_saved_history(previous, current, archived_ids=(), *, state=None):
    """Retain confirmed editions/events, allowing explicitly archived failed reviews."""
    archived = set(archived_ids)
    rejected = {(row['snapshot'], row['revision_id'], row['document_url'])
                for row in current.get('rejected_binding_observations', [])}
    def rejected_observation(row):
        return (row['snapshot'], row['revision_id'], row['source'].get('document_url')) in rejected

    old_events = {e["revision_id"]: e for e in previous.get("updates", [])}
    event_sources = previous.get('update_sources', {})
    events = {e["revision_id"]: e for e in current.get("updates", [])}
    superseded = archived | {rid for rid, event in old_events.items()
                            if (event['snapshot'], rid, event_sources.get(rid, {}).get('document_url')) in rejected
                            and events.get(rid, {}).get('snapshot') != event['snapshot']}
    owners = {r['revision_id']: r for r in [*current.get('revoked_revisions', ()), *current.get('revisions', ())]}
    restored = {}
    replaced_observations = set()
    for rejection in previous.get('rejected_binding_observations', ()):
        rid, snapshot = rejection['revision_id'], rejection['snapshot']
        address = snapshot, rid, rejection.get('document_url')
        if address in rejected:
            continue
        evidence = rejection.get('source_observation')
        owner = owners.get(rid, {})
        source_keys = ('environment', 'number', 'document_url', 'document_sha256')
        incomplete = evidence is None or not rejection.get('resolved_schema_graph') or 'namespace' not in rejection
        if incomplete:
            if state is None:
                raise ValueError('Для восстановления требуются архивные доказательства наблюдения')
            evidence = archived_restoration(state, rejection, owners)
        if (not evidence or (evidence['snapshot'], evidence['revision_id']) != (snapshot, rid)
                or evidence['observed_at'] != rejection['observed_at']
                or any(evidence['source'].get(k) != rejection.get(k) for k in source_keys)
                or (rejection.get('resolved_schema_graph') and owner.get('resolved_schema_graph') != rejection['resolved_schema_graph'])
                or ('namespace' in rejection and schema_qname(owner) != schema_qname(rejection))):
            raise ValueError('Не подтверждены исходные данные восстановленной привязки')
        # An earlier accepted observation may already represent this same source.
        old_observation = next((r for r in previous.get('source_observations', ()) if r['revision_id'] == rid
                                and all(r['source'].get(k) == rejection.get(k) for k in source_keys)), None)
        now = next((r for r in current.get('source_observations', ()) if r['revision_id'] == rid
                    and all(r['source'].get(k) == rejection.get(k) for k in source_keys)), None)
        if now == old_observation and now is not None:
            continue
        if now != evidence:
            raise ValueError('Изменилось исходное наблюдение восстановленной привязки')
        restored[address] = evidence
        if old_observation:
            replaced_observations.add((old_observation['snapshot'], rid, rejection['document_url']))
        if events.get(rid, {}).get('snapshot') == snapshot:
            event = events[rid]
            if (any(event.get(k) != evidence[k] for k in ('revision_id', 'snapshot', 'observed_at', 'initial_import'))
                    or current['update_sources'].get(rid) != evidence['source']):
                raise ValueError('Изменилось событие восстановленной привязки')
            superseded.add(rid)
    old_ids = {r["revision_id"] for r in previous.get("revisions", [])} - superseded
    new_ids = {r["revision_id"] for r in current.get("revisions", [])}
    if not old_ids <= new_ids:
        raise ValueError("Обновление потеряло сохраненные редакции")
    new_rows = {row['revision_id']: row for row in current.get('revisions', [])}
    for row in [*previous.get('revisions', []), *previous.get('revoked_revisions', [])]:
        rid = row['revision_id']
        graph = row.get('resolved_schema_graph')
        if rid in owners and (graph or row.get('status') == 'matched'):
            if graph and owners[rid].get('resolved_schema_graph') != graph:
                raise ValueError("Изменился граф XSD существующей редакции " + rid)
            if 'namespace' in row and schema_qname(owners[rid]) != schema_qname(row):
                raise ValueError("Изменился QName корня существующей редакции " + rid)
            if 'namespace' not in row and row.get('root') != owners[rid].get('root'):
                raise ValueError("Изменился корень существующей редакции " + rid)
    if len(events) != len(current.get("updates", [])) or set(events) != new_ids:
        raise ValueError("Журнал не соответствует сохранённым редакциям")
    predecessors = update_predecessors(current.get('updates', []), new_rows.values())
    if any(event.get('previous_revision_id') != predecessors[rid] for rid, event in events.items()):
        raise ValueError("Некорректная ссылка сравнения в журнале сохранённых наблюдений")
    retained = old_events.keys() - superseded
    if [rid for rid in old_events if rid in retained] != [rid for rid in events if rid in retained]:
        raise ValueError("Изменился порядок ранее сохранённых событий журнала")
    old_raw = previous.get('original_update_events', {})
    raw = current.get('original_update_events', {})
    archived_events = {row['revision_id']: row['update_event'] for row in current.get('rejected_binding_observations', [])
                       if 'update_event' in row}
    for prior in previous.get('rejected_binding_observations', ()):
        if 'update_event' in prior:
            rid = prior['revision_id']
            if raw.get(rid, archived_events.get(rid, events.get(rid))) != prior['update_event']:
                raise ValueError('Изменилось исходное событие журнала')
    for rid, event in old_events.items():
        if rid in archived:
            continue
        if rid not in superseded and events.get(rid) != dict(event, previous_revision_id=predecessors[rid]):
            raise ValueError("Изменилось ранее сохранённое событие журнала")
        original = raw.get(rid, archived_events.get(rid, events.get(rid)))
        # Pre-upgrade catalogs with rejections stored only the effective link.
        if (rid in old_raw or not previous.get('rejected_binding_observations')) and (
                event.get('previous_revision_id') not in archived and original != old_raw.get(rid, event)):
            raise ValueError("Изменилось исходное событие журнала")
    def enriched(old, new):
        # Word history is restored from the original bytes without changing the
        # evidence already recorded by earlier catalog formats.
        return old == {k: v for k, v in new.items() if k != "document_history" or k in old}
    for rid, source in previous.get("update_sources", {}).items():
        rejected_source = (old_events[rid]['snapshot'], rid, source.get('document_url')) in rejected
        if rid not in superseded and not rejected_source and not enriched(source, current.get("update_sources", {}).get(rid, {})):
            raise ValueError("Изменился ранее сохранённый источник события журнала")
    observations = [row for row in current.get("source_observations", []) if row["revision_id"] not in archived]
    old_observations = [row for row in previous.get("source_observations", [])
                        if row["revision_id"] not in archived and not rejected_observation(row)]
    if restored:
        def address(row):
            return row['snapshot'], row['revision_id'], row['source'].get('document_url')
        old_observations = [r for r in old_observations if address(r) not in replaced_observations]
        observations = [r for r in observations if address(r) not in restored]
    if rejected:
        def identity(row):
            return row['snapshot'], row['revision_id'], row['source'].get('document_url')
        old_sources = {identity(row) for row in old_observations}
        observations = [row for row in observations if identity(row) in old_sources]
    for old, new in zip(old_observations, observations):
        if ({k: v for k, v in old.items() if k != "source"} != {k: v for k, v in new.items() if k != "source"}
                or not enriched(old["source"], new["source"])):
            raise ValueError("Изменились ранее сохранённые наблюдения источников")
    if len(observations) < len(old_observations):
        raise ValueError("Изменились ранее сохранённые наблюдения источников")


def validate_history(previous, current, directory):
    validate_publications(previous, current)
    validate_saved_history(previous, current, state=directory / "state")
    old_ids = {r["revision_id"] for r in previous["revisions"]}
    new_ids = {r["revision_id"] for r in current["revisions"]}
    if current["review"]:
        raise ValueError("Новые схемы требуют проверки сопоставления")
    status = json.loads((directory / "data/fields/index.json").read_bytes())
    ready = {r["revision_id"] for r in status if r["status"] == "ready"}
    documents = {r["revision_id"] for r in current["revisions"] if r["status"]=="document_only" and r.get("source_type")=="manual"}
    if ready != new_ids-documents or {r["revision_id"] for r in status if r["status"]=="document_only"}!=documents:
        raise ValueError("Не все редакции удалось построить")
    for revision in current["revisions"]:
        rid = revision["revision_id"]
        if rid in documents:
            continue
        fields = json.loads((directory / "data/fields" / (rid + ".json")).read_bytes())["fields"]
        if not fields or fields[0]["name"] != revision["root"]:
            raise ValueError("Некорректный корень " + rid)
        for idx, field in enumerate(fields):
            if any(fields[child]["parent"] != idx for child in field["children"]):
                raise ValueError("Повреждена связь полей " + rid)
        if not (directory / "site/editions" / (rid + ".html")).is_file():
            raise ValueError("Не создана страница " + rid)
    for rid in old_ids & new_ids:
        old_field = directory / "previous-fields" / (rid + ".json")
        new_field = directory / "data/fields" / (rid + ".json")
        old_revision=next(r for r in previous["revisions"] if r["revision_id"]==rid)
        if old_revision["status"]=="document_only" and old_revision.get("source_type")=="manual":
            continue
        if old_field.read_bytes() != new_field.read_bytes():
            raise ValueError("Изменилось дерево существующей редакции " + rid)
    revoked = {r['revision_id'] for r in current.get('revoked_revisions', [])}
    for env in ("prod", "test"):
        old_current = [r for r in previous["revisions"] if r["environment"] == env and r.get("listed_now", True)
                       and r['revision_id'] not in revoked]
        new_current = [r for r in current["revisions"] if r["environment"] == env and r.get("listed_now", True)]
        # A disappearing document is retained in history; an empty contour needs investigation.
        if old_current and not new_current:
            raise ValueError("Исчезли все актуальные тома контура " + env)


def build_history_baseline(stage):
    """Recompute the confirmed inputs with the generator used for the candidate."""
    build(stage / "data/catalog.json", stage / "state", stage / "previous-fields")


def recover_promotion(root):
    recovery = root / ".update-recovery"
    if not recovery.exists():
        return
    if recovery.is_symlink() or getattr(recovery, "is_junction", lambda: False)():
        raise ValueError("Ссылка на каталог восстановления " + str(recovery))
    if (recovery / "cleanup.json").is_file():
        build_site.cleanup_site_recovery(recovery)
        return
    allowed = {"journal.json", "journal.tmp", "cleanup.partial", "committed", "preparing", "previous", "candidate"}
    for entry in recovery.iterdir():
        if entry.name not in allowed or entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            raise ValueError("Неизвестное содержимое каталога восстановления " + str(recovery))
    if any(recovery.iterdir()) and not any((recovery / name).is_file() for name in ("journal.json", "committed", "preparing")):
        raise ValueError("Отсутствуют признаки транзакции восстановления " + str(recovery))
    for directory in ("previous", "candidate"):
        path = recovery / directory
        if path.exists():
            for entry in path.iterdir():
                if entry.name not in ("state", "data", "site") or not entry.is_dir():
                    raise ValueError("Неизвестное содержимое каталога восстановления " + str(path))
    for entry in recovery.rglob("*"):
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            raise ValueError("Ссылка внутри каталога восстановления " + str(recovery))
    journal = recovery / "journal.json"
    if journal.exists() and not (recovery / "committed").exists():
        existed = json.loads(journal.read_bytes())
        for folder in ("state", "data", "site"):
            backup, target = recovery / "previous" / folder, root / folder
            if backup.exists():
                if target.exists():
                    shutil.rmtree(target)
                backup.replace(target)
            elif not existed[folder] and target.exists():
                shutil.rmtree(target)
    # Atomically record ownership before deleting any transaction evidence.
    # A retry resumes this manifest and never replays rollback after cleanup began.
    build_site.cleanup_site_recovery(recovery)


def promote(root, stage):
    """Keep a recoverable previous generation until every directory is installed."""
    recover_promotion(root)
    build_site.preserve_publication_files(root / "site", stage / "site")
    recovery = root / ".update-recovery"
    recovery.mkdir()
    (recovery / "preparing").touch()
    try:
        for folder in ("state", "data", "site"):
            shutil.copytree(stage / folder, recovery / "candidate" / folder)
        (recovery / "previous").mkdir()
        existed = {folder: (root / folder).exists() for folder in ("state", "data", "site")}
        temporary = recovery / "journal.tmp"
        temporary.write_bytes(encode(existed))
        temporary.replace(recovery / "journal.json")
        for folder in existed:
            if existed[folder]:
                (root / folder).replace(recovery / "previous" / folder)
            (recovery / "candidate" / folder).replace(root / folder)
        (recovery / "committed").touch()
    except Exception:
        recover_promotion(root)
        raise
    recover_promotion(root)


@contextmanager
def update_lock(root, *, filename=".update.lock", recover=True, catalog_pair=None):
    with file_lock(root, filename=filename) as root:
        if recover:
            recover_promotion(root)
            build_site.recover_site(root)
            from sync import confirmation_lock, recover_confirmation
            state = root / "state"
            if (root / ".catalog-recovery").is_dir() and not (catalog_pair and catalog_pair[0] == root):
                raise ValueError("Завершите восстановление update_catalog.py с исходными --state и --out")
            if (state / ".catalog-recovery").is_dir() and not (catalog_pair and catalog_pair[0] == state):
                from update_catalog import recover_catalog
                recover_catalog(state, root / "data")
            if (state / ".sync-recovery.json").exists():
                with confirmation_lock(state):
                    recover_confirmation(state)
        yield root


@contextmanager
def catalog_locks(*directories, catalog_pair=None):
    """CLI writers sharing a managed state, data or site directory share its project lock."""
    roots = {catalog_lock_root(directory) for directory in directories}
    with ExitStack() as locks:
        for root in sorted(roots, key=str):
            root.mkdir(parents=True, exist_ok=True)
            locks.enter_context(update_lock(root, catalog_pair=catalog_pair))
        yield


def run(root):
    with update_lock(root) as locked_root:
        _run(locked_root)


def edition_changes(previous, current, fields_directory, *, journal):
    """Use the site's chronology for new editions and report newly imported sources."""
    statuses = json.loads((fields_directory / "index.json").read_bytes())
    ready = {row["revision_id"] for row in statuses if row["status"] == "ready"}
    fields = {}
    def load(rid):
        if rid not in fields:
            fields[rid] = json.loads((fields_directory / (rid + ".json")).read_bytes())["fields"]
        return fields[rid]
    def grouped(catalog):
        return group_editions(dict(catalog, revisions=[dict(row, ready=row["revision_id"] in ready)
                                                       for row in catalog["revisions"]]), load)
    old, new = grouped(previous), grouped(current)
    old['updates'] = build_site.summarize_journal(old, fields_directory)
    old_schemas = {event["revision_id"] for event in display_updates(old)}
    old_sources = {event["event_id"] for event in source_updates(old)}
    displayed = dict(new, updates=journal)
    schemas = [{key: value for key, value in event.items() if key != "comparison"}
               for event in display_updates(displayed) if event["revision_id"] not in old_schemas]
    sources = [event for event in source_updates(displayed) if event["event_id"] not in old_sources]
    return schemas, sources


def _run(root, *, history_only=False):
    report = root / "update-report.json"
    inventory = {}
    previous = json.loads((root / "data/catalog.json").read_bytes())
    try:
        with tempfile.TemporaryDirectory(prefix="sedo-update-") as tmp:
            stage = Path(tmp)
            for folder in ("state", "data", "web"):
                shutil.copytree(root / folder, stage / folder)
            build_history_baseline(stage)
            shutil.copy2(root / "viewer-template.html", stage / "viewer-template.html")
            failure = stage / "state/last-run.json"
            prior_report = failure.read_bytes() if failure.is_file() else None
            prior_snapshot = active_inventory(root / "state")
            try:
                if history_only:
                    from collector_snapshots import replay_archived_inputs
                    collector_replay = replay_archived_inputs(stage / "state")
                else:
                    sync(stage / "state")
            except Exception as exc:
                report_bytes = failure.read_bytes() if failure.is_file() else None
                failed_run = json.loads(report_bytes) if report_bytes and report_bytes != prior_report else {}
                failed_snapshot = failed_run.get('snapshot')
                if failed_run.get('status') == 'failed' and re.fullmatch(r'[0-9a-f]{64}', failed_snapshot or ''):
                    candidate = stage / 'state/snapshots' / (failed_snapshot + '.json')
                    if candidate.is_file():
                        inventory.update(inventory_changes(prior_snapshot, json.loads(candidate.read_bytes())))
                detail = failed_run.get("errors", [])
                raise ValueError(f"{exc}; source errors: {detail}") from exc
            snapshot = active_inventory(stage / "state")
            validate_inventory(prior_snapshot, snapshot, diagnostics=inventory)
            export(stage / "state", stage / "data/catalog.json")
            current = json.loads((stage / "data/catalog.json").read_bytes())
            validate_published_trees(prior_snapshot, snapshot, state=root / "state", candidate_state=stage / "state")
            validate_publications(previous, current)
            if not history_only:
                current["checked_at"] = json.loads((stage / "state/last-run.json").read_bytes())["observed_at"]
            else:
                current["checked_at"] = previous.get("checked_at") or current.get("checked_at")
            write(stage / "data/catalog.json", encode(current))
            build(stage / "data/catalog.json", stage / "state", stage / "data/fields")
            with patch.object(build_site, "HERE", stage):
                journal = build_site.main()
            validate_history(previous, current, stage)
            old_ids = {r["revision_id"] for r in previous["revisions"]}
            added = [dict(number=r["number"], environment=r["environment"], version=r["document_version"], revision_id=r["revision_id"])
                     for r in current["revisions"] if r["revision_id"] not in old_ids]
            schema_events, source_events = edition_changes(previous, current, stage / "data/fields", journal=journal)
            known = {(r["environment"], r["number"]) for r in previous["registry"]+previous["revisions"]}
            new_messages = sorted({(r["environment"], r["number"]) for r in current["registry"]+current["revisions"]}-known)
            if not history_only:
                collector_replay=json.loads((stage/"state/last-run.json").read_bytes()).get("collector_replay", [])
            diagnostics = {}
            for document in snapshot['entries']:
                # Single-declaration historical formats retain candidate errors
                # beside a successful match. Only unresolved declarations belong in the report.
                matches = {(m['number'], m['root']) for m in document.get('module_matches', ())}
                bindings = document.get('module_bindings', ())
                single = {b['number'] for b in bindings if sum(c['number'] == b['number'] for c in bindings) == 1}
                issues = [i for i in document.get('schema_diagnostics', ()) if i.get('number') not in single
                          or (i.get('number'), i.get('root')) not in matches]
                if issues:
                    diagnostics[document['environment'], document['url']] = issues
            document_failures = {}
            for r in current.get('document_sources', []):
                outcome = r.get('schema_diagnostic')
                if not r.get('listed_now', True) or (not outcome and 'namespace_declared' not in r):
                    continue
                env, number = r['environment'], r['number']
                issue = dict(number=number, root=r['root'], reason=r['schema_reason'])
                source_issues = diagnostics.setdefault((env, r['document_url']), [])
                if outcome:
                    issue.update(outcome)
                    # The catalog gives the effective result for this active
                    # declaration. Raw inventory diagnostics can now be stale.
                    same_document = next((doc for doc in snapshot['entries'] if doc['environment'] == env
                        and doc['url'] == r['document_url'] and doc.get('sha256') == r['document_sha256']), {})
                    same_root = {decl.get('namespace') for decl in same_document.get('module_bindings', ())
                                 if (decl['number'], decl.get('root')) == (number, r['root'])}
                    source_issues[:] = [other for other in source_issues
                        if (other.get('number'), other.get('root')) != (number, r['root'])
                        or ('code' in other and other.get('resolved_schema_graph') != outcome.get('resolved_schema_graph'))
                        or (other.get('namespace') != outcome['declared_namespace'] if 'namespace' in other
                            else len(same_root) != 1)]
                if issue not in source_issues:
                    source_issues.append(issue)
                document_failures.setdefault((env, number), []).append(r)
            schema_diagnostics = [dict(environment=env, source=url, issues=issues)
                                  for (env, url), issues in diagnostics.items()]
            wsdl_status=[dict(environment=r["environment"],source=r["url"],status=r.get("status",r.get("retrieval_status","ok")),error=r.get("error")) for r in snapshot["entries"] if r.get("source_type")=="wsdl" or r["kind"]=="wsdl_status"]
            ready_pairs={(r["environment"],r["number"]) for r in current["revisions"] if r["status"]=="matched" and r.get("listed_now", True)}
            registry_pairs={(r["environment"],r["number"]):r for r in current["registry"] if r.get("listed_now", True)}
            for pair, documents in document_failures.items():
                registry_pairs.setdefault(pair, documents[0])
            missing=[]
            for (env,number),row in sorted(registry_pairs.items(),key=lambda i:(i[0][0],int(i[0][1]))):
                if (env,number) in ready_pairs:continue
                reasons=list(dict.fromkeys(r['schema_reason'] for r in document_failures.get((env, number), [])))
                if not reasons:
                    reasons=list(dict.fromkeys(issue["reason"] for record in schema_diagnostics if record["environment"]==env for issue in record["issues"] if issue.get("number")==number))
                for source in wsdl_status:
                    if source["environment"]==env and source["status"]=="unavailable":
                        reasons.append("WSDL недоступен: "+source["source"].split("#")[0]+" — "+source["error"])
                missing.append(dict(environment=env,number=number,title=row["title"],reasons=reasons or ["В опубликованных источниках не найдена полная сопоставленная XSD."]))
            write(stage/"data/schema-coverage.json",encode(dict(checked_at=current["checked_at"],numbers_with_trees=len({n for e,n in ready_pairs}),source_checks=wsdl_status,missing=missing)))
            promote(root, stage)
            result = dict(status="ok", collector_source=collector_source(), inventory=inventory,
                          checked_at=current["checked_at"], new_revisions=added, schema_diagnostics=schema_diagnostics, wsdl_status=wsdl_status, missing_trees=missing,
                          collector_replay=collector_replay,
                          new_schema_revisions=schema_events, new_source_events=source_events,
                          new_messages=[dict(environment=e, number=n) for e,n in new_messages],
                          total_revisions=len(current["revisions"]))
            if current.get('revoked_revisions'):
                result['revoked_revisions'] = current['revoked_revisions']
            write(report, encode(result))
            print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        write(report, encode(dict(status="failed", error=str(exc), inventory=inventory)))
        raise


if __name__ == "__main__":
    run(Path(__file__).resolve().parent)

