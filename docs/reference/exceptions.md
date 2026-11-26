# Exceptions reference

The API renders domain exceptions as HTTP problem details. See the [exception classes](../../errors.py) and [HTTP handlers](../../http_errors.py).

---

## Table of Contents

1. [Error responses](#error-responses)
2. [Exception classes](#exception-classes)
3. [Usage](#usage)

---

## Error responses

Every error response is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details object, served as `application/problem+json`:

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "detail": "Text with ID 'abc' not found"
}
```

- `type` is always `about:blank`, so `title` is the standard phrase of the HTTP status.
- `status` repeats the HTTP status code.
- `detail` is the human-readable message for this occurrence.

Validation failures (422) add `errors`, one entry per failed field. `detail` repeats the first entry's message.

```json
{
  "type": "about:blank",
  "title": "Unprocessable Content",
  "status": 422,
  "detail": "Input should be a valid string",
  "errors": [{"detail": "Input should be a valid string", "pointer": "#/body/bdrc"}]
}
```

For request input, `pointer` starts with where the value came from (`#/body/...`, `#/query/...`, `#/path/...`, `#/header/...`). For a document validated inside a handler, such as the recording metadata form field, it points into that document (`#/title`).

| Source | Status |
|--------|--------|
| `OpenPechaError` subclasses | The class's `status_code` |
| Invalid request input, or a Pydantic `ValidationError` | 422, with `errors` |
| Neo4j trigger failures / constraint violations / other client errors | 422 / 409 / 400 |
| OpenSearch errors | 503 |
| Unknown routes and disallowed methods | 404 / 405 |
| Anything else | 500 |

---

## Exception classes

All API errors raised by the OpenPecha backend inherit from `OpenPechaError`, which carries a `message` and a class-level `status_code` (default 500). Subclasses override `status_code` only.

| Class | status_code | Typical use |
|-------|-------------|-------------|
| **InvalidRequestError** | 400 | Bad request (e.g. malformed input). |
| **UnauthorizedError** | 401 | Invalid or missing API key, or API key not authorized for the requested application. |
| **ForbiddenError** | 403 | Application administration with an application-bound API key. |
| **DataNotFoundError** | 404 | Resource missing (e.g. text, person, edition not found). |
| **DataConflictError** | 409 | Conflict (e.g. duplicate resource, resource still referenced). |
| **DataValidationError** | 422 | Business or database validation failure (e.g. referenced person does not exist). |
| **ServiceUnavailableError** | 503 | A dependency such as search is unavailable. |

---

## Usage

Raise the appropriate subclass where the failure occurs, e.g. `raise DataNotFoundError("Text with ID 'abc' not found")`. The HTTP handler uses the class's `status_code` and the exception's message as `detail`.
