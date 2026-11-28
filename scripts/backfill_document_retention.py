"""Audit S3 retention tags; writes only with --apply after the additive migration.

Run with the backend virtual environment, DATABASE_URL and CONVERSION_S3_BUCKET.
No object contents, names, owner identifiers or database credentials are printed.
"""
import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


def classify_key(key, paid_inputs, paid_analyses, input_prefix, result_prefix):
    if key.startswith(input_prefix + '/'):
        relative = key[len(input_prefix) + 1:]
        if relative.startswith('pro-fiscal/'):
            return 'paid'  # Fiscal maintenance owns deletion, including old imports.
        if re.fullmatch(r'doc_[a-f0-9]{24}/document\.[a-z0-9]{1,10}', relative):
            return 'paid' if relative.split('/')[0] in paid_inputs else 'temporary'
    if key.startswith(result_prefix + '/'):
        parts = key[len(result_prefix) + 1:].split('/')
        if parts[0] == 'canonical' and len(parts) > 2:
            return 'paid'  # Canonical cleanup owns all versions and failed exports.
        if len(parts) == 2 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', parts[0]):
            return 'paid' if parts[0] in paid_analyses else 'temporary'
    return None


def main():
    import boto3
    import psycopg
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    schema = os.getenv('DATABASE_SCHEMA', 'public')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', schema):
        raise ValueError('Invalid schema')
    paid_inputs, paid_analyses = set(), set()
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        for document, analysis, attempts in conn.execute(f'''SELECT document,result_analysis_id,retained_analysis_ids
                FROM "{schema}".conversion_jobs WHERE product='pro' '''):
            paid_inputs.add(document['storage_key'])
            paid_analyses.update(attempts)
            if analysis:
                paid_analyses.add(analysis)
    bucket = os.environ['CONVERSION_S3_BUCKET']
    inputs = os.getenv('CONVERSION_S3_PREFIX', 'conversion/jobs').strip('/')
    results = os.getenv('CONVERSION_RESULTS_S3_PREFIX', 'conversion/results').strip('/')
    client, counts = boto3.client('s3'), Counter()
    for prefix in (inputs, results):
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix + '/'):
            for item in page.get('Contents', []):
                kind = classify_key(item['Key'], paid_inputs, paid_analyses, inputs, results)
                if kind is None:
                    counts['unrecognized'] += 1
                    continue
                tags = {entry['Key']: entry['Value'] for entry in client.get_object_tagging(Bucket=bucket, Key=item['Key'])['TagSet']}
                # Never demote a paid artifact if a source is concurrently created.
                if tags.get('ofx-retention') == 'paid':
                    counts['already_paid'] += 1
                    continue
                if tags.get('ofx-retention') == kind:
                    counts['unchanged'] += 1
                    continue
                counts[kind] += 1
                if args.apply:
                    tags['ofx-retention'] = kind
                    client.put_object_tagging(Bucket=bucket, Key=item['Key'],
                        Tagging={'TagSet': [{'Key': key, 'Value': value} for key, value in tags.items()]})
    print({'mode': 'applied' if args.apply else 'audit', **counts})


if __name__ == '__main__':
    main()
