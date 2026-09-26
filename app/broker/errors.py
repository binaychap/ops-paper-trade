"""Public SDK error fields without request headers or raw payloads."""

import os
import re

from webull.core.exception.exceptions import ClientException, ServerException


def describe_webull_error(exc):
    if not isinstance(exc, (ClientException, ServerException)):
        return type(exc).__name__
    fields = [type(exc).__name__]
    for label, attribute in (('HTTP', 'http_status'), ('code', 'error_code'),
                             ('reason', 'error_msg')):
        value = getattr(exc, attribute, None)
        if value is not None and value != '':
            text = str(value)
            for key, secret in os.environ.items():
                if secret and any(part in key.upper() for part in
                                  ('SECRET', 'TOKEN', 'PASSWORD', 'API_KEY', 'APP_KEY', 'ACCOUNT_ID')):
                    text = text.replace(secret, '[redacted]')
            text = re.sub(r'(?i)(x-app-key|x-signature|authorization|access_token|account_id)'
                          r'([\s\"\x27:=]+)[^\s,;}]+', r'\1\2[redacted]', text)
            fields.append(f'{label}={" ".join(text.split())[:500]}')
    return '; '.join(fields)
