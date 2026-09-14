"""Plan and apply explicitly reviewed source groups through the IAP console API."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .operator_submission import locked
from .source_merges import FORMAT_VERSION, configuration
from .subject_management import subject_uuid


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ConsoleClient:
    def __init__(self, origin, token_file=None, impersonate_service_account=None):
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Use the HTTPS origin of the authenticated console.')
        self.origin = origin.rstrip('/')
        self.token_file = token_file
        self.impersonate_service_account = impersonate_service_account
        self.opener = build_opener(NoRedirect())

    def post(self, path, data):
        if self.impersonate_service_account:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
            account = self.impersonate_service_account
            issued = int(time.time())
            payload = {'iss': account, 'sub': account, 'aud': self.origin + path,
                       'iat': issued, 'exp': issued + 600}
            with AuthorizedSession(credentials) as session:
                response = session.post('https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'
                                        + quote(account, safe='') + ':signJwt',
                                        json={'payload': json.dumps(payload)}, timeout=30)
                if not response.ok:
                    raise ValueError(f'Keyless IAP token signing failed (HTTP {response.status_code}). Check operator impersonation access.')
                token = response.json()['signedJwt']
        elif self.token_file:
            token = self.token_file.read_text().strip()
        else:
            import google.auth
            from google.auth.transport.requests import Request as AuthRequest
            credentials, _ = google.auth.default()
            credentials.refresh(AuthRequest())
            token = getattr(credentials, 'id_token', None)
            if not token:
                raise ValueError('Use user application-default credentials or provide an IAP-authorized identity token file.')
        request = Request(self.origin + path, data=json.dumps(data).encode(), method='POST', headers={
            'Authorization': 'Bearer ' + token,
            'Origin': self.origin, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as error:
            raise ValueError(f'Console returned HTTP {error.code}; check authentication or the plan. Original operation IDs are retained for retry.') from error


def save_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def plan_sources(client, sources, threshold, output):
    ids = [subject_uuid(s) for s in sources]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Provide explicit, unique source IDs.')
    configuration({'threshold': threshold})
    with locked(output):
        if output.exists():
            raise ValueError('Plan file already exists; choose a new output path for a rescan.')
        document = {'format_version': FORMAT_VERSION, 'origin': client.origin, 'plans': [], 'failures': []}
        save_private(output, document)
        for source in ids:
            try:
                document['plans'].append(client.post(f'/api/sources/{source}/merge-proposals', {'threshold': threshold}))
            except (ValueError, OSError) as error:
                document['failures'].append({'source_id': source, 'message': str(error)})
            save_private(output, document)
        return document


def apply_plan(client, path, receipt):
    if path.resolve() == receipt.resolve():
        raise ValueError('The receipt must be separate from the input plan.')
    with locked(receipt):
        document = json.loads(path.read_text())
        if document.get('format_version') != FORMAT_VERSION or document.get('origin') != client.origin:
            raise ValueError('Unsupported plan or console origin mismatch.')
        state = json.loads(receipt.read_text()) if receipt.exists() else {
            'format_version': FORMAT_VERSION, 'origin': client.origin, 'operations': {}}
        if state.get('format_version') != FORMAT_VERSION or state.get('origin') != client.origin:
            raise ValueError('Unsupported receipt or console origin mismatch.')
        for plan in document['plans']:
            source = subject_uuid(plan['source_id'])
            for group in plan['groups']:
                if group.get('selected') is not True:
                    continue
                operation = subject_uuid(group['operation_id'])
                # Save the exact request before sending it. On retry it cannot be edited.
                request = {key: plan[key] for key in ('format_version', 'algorithm', 'source_id', 'threshold')}
                request['groups'] = [group]
                entry = state['operations'].get(operation)
                if entry and entry['request'] != request:
                    raise ValueError('A submitted operation changed. Restore its original payload from the receipt before retrying.')
                if entry and entry.get('outcome', {}).get('status') == 'merged':
                    continue
                entry = {'request': request, 'outcome': {'status': 'pending'}}
                state['operations'][operation] = entry
                save_private(receipt, state)
                try:
                    result = client.post(f'/api/sources/{source}/merge-proposals/apply', request)
                    entry['outcome'] = result['outcomes'][0]
                except (ValueError, OSError) as error:
                    entry['outcome'] = {'status': 'failed', 'code': 'operation_uncertain', 'message': str(error)}
                save_private(receipt, state)
        return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument('--impersonate-service-account', help='Keyless IAP operator service account; use your application-default credentials to sign a short-lived request JWT')
    auth.add_argument('--token-file', type=Path, help='Private file containing an IAP-authorized bearer token')
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    sources = plan.add_mutually_exclusive_group(required=True)
    sources.add_argument('--source', action='append')
    sources.add_argument('--sources-file', type=Path, help='JSON array of explicit source UUIDs')
    plan.add_argument('--threshold', required=True, type=float)
    plan.add_argument('--output', required=True, type=Path)
    apply = commands.add_parser('apply')
    apply.add_argument('--input', required=True, type=Path)
    apply.add_argument('--receipt', required=True, type=Path)
    apply.add_argument('--reviewed', required=True, action='store_true', help='Attest that selected groups and survivor identities were reviewed')
    args = parser.parse_args(argv)
    client = ConsoleClient(args.origin, args.token_file, args.impersonate_service_account)
    if args.command == 'plan':
        result = plan_sources(client, args.source or json.loads(args.sources_file.read_text()), args.threshold, args.output)
        print(f"Saved {len(result['plans'])} source plans; {len(result['failures'])} failures. Groups start unselected.")
        return 1 if result['failures'] else 0
    result = apply_plan(client, args.input, args.receipt)
    counts = {}
    for entry in result['operations'].values():
        status = entry['outcome']['status']
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps(counts, sort_keys=True))
    return 1 if any(s != 'merged' for s in counts) else 0


if __name__ == '__main__':
    raise SystemExit(main())
