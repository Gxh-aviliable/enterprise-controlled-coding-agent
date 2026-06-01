---
name: fastapi
description: FastAPI patterns, middleware, dependency injection, and SSE streaming
---

# FastAPI Development Patterns

## Application Structure
- Use `lifespan` for startup/shutdown resource management
- Register routers with `app.include_router()`
- Use `APIRouter(prefix="/path", tags=["tag"])` for route grouping

## Middleware
- CORS: always pair `allow_credentials=True` with specific origins, never `*`
- Authentication: use FastAPI `Depends(get_current_user)` dependency injection
- Error handling: use `@app.exception_handler(Exception)` for global catch-all

## Dependencies
- Use `Depends()` for DB sessions, auth, config
- Async dependencies return awaitables: `async def get_db()`
- Don't mix sync and async dependencies

## Request/Response
- Pydantic models for request validation (`BaseModel`)
- Use `response_model` for automatic response serialization
- Handle validation errors with custom exception handlers

## SSE Streaming
```python
async def generate():
    try:
        async for chunk in stream:
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"
    except GeneratorExit:
        return  # Never yield in GeneratorExit handler!
```
- Set `media_type="text/event-stream"`
- Use `StreamingResponse(generate(), ...)`
- NEVER `yield` inside a `GeneratorExit` handler — causes RuntimeError

## Security
- Hash passwords with `bcrypt` or `argon2`
- JWT tokens with short expiry + refresh token
- Validate all input, even from authenticated users
