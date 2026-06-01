---
name: python
description: Python coding standards, best practices, and patterns
---

# Python Coding Guidelines

## Style
- Follow PEP 8 conventions
- Use type hints for all function signatures
- Use `pathlib.Path` for file paths, not `os.path`
- Use f-strings for formatting, not `%` or `.format()`

## Project Structure
- Use `src/` layout for packages
- Keep `__init__.py` minimal with explicit exports
- Separate concerns: API, core business logic, infrastructure

## Error Handling
- Never use bare `except:`
- Use specific exception types
- Log errors with context, not just the exception message

## Async
- Use `async/await` for I/O-bound operations
- Don't mix `asyncio` with synchronous blocking calls
- Use `asyncio.gather()` for parallel async operations

## Testing
- Use pytest with fixtures
- Test both happy path and edge cases
- Mock external dependencies, not internal functions
