# Password Reset Dev Mode Design

## Goal

Add a forgot-password flow that lets a user request a verification code by email address and reset their password with that code. For local development, the code is written to the backend log instead of requiring SMTP credentials.

## Scope

- Add two unauthenticated API endpoints:
  - `POST /auth/forgot-password` accepts an email and starts a reset request.
  - `POST /auth/reset-password` accepts email, code, and new password.
- Store reset codes in Redis with a 10 minute TTL.
- Return the same success message whether the email exists or not, so the API does not reveal registered emails.
- In development mode without SMTP config, log the code with the target email.
- Add a compact forgot-password flow inside the existing login card.

## Architecture

The auth route owns password reset orchestration. A small email helper decides whether SMTP is configured; when it is not, it logs the code for local testing. Redis stores short-lived verification codes under `password_reset:{normalized_email}`. MySQL remains the source of truth for users and password hashes.

## Data Flow

1. User clicks `Forgot password?`.
2. User enters their email.
3. Backend looks up the active user. If found, it generates a 6 digit code, stores it in Redis, and sends/logs it.
4. Frontend asks for code and new password.
5. Backend compares the submitted code with Redis using constant-time comparison.
6. Backend updates `User.password_hash`, deletes the Redis code, and returns success.

## Configuration

SMTP is optional. If these values are absent, development logging is used:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `SMTP_USE_SSL`
- `PASSWORD_RESET_CODE_TTL_SECONDS`

## Error Handling

- Requesting a code always returns a generic success message.
- Reset with an invalid or expired code returns HTTP 400.
- Reset for a disabled or missing user returns HTTP 400.
- New passwords use the existing password length validation rules.

## Testing

- Backend route tests cover generic request behavior, Redis code storage, invalid code rejection, successful password update, and one-time code deletion.
- Frontend build verifies the new form compiles.

