"""Run only against an explicitly supplied disposable database, never application settings."""

import asyncio
import os
import subprocess
import sys
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.models.admin import SharedSkill, SharedSkillVersion
from enterprise_agent.skills.registry import publish
from tests.skills.test_packages import CONTENT, package

pytestmark = pytest.mark.integration


async def test_mysql_concurrent_publish_and_process_refresh():
    url = os.environ.get("SKILL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SKILL_TEST_DATABASE_URL must target a disposable migrated database")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    name = "probe-" + uuid.uuid4().hex[:12]
    content = CONTENT.replace("name: sample", "name: " + name)
    skill_id = None
    try:
        async with factory() as db:
            skill = SharedSkill(name=name, draft_content=content, draft_package=package(content), revision=1)
            db.add(skill)
            await db.commit()
            skill_id = skill.id

        async def release():
            async with factory() as db:
                try:
                    return await publish(db, name, None, None, changelog="concurrent release", expected_revision=1)
                except HTTPException as exc:
                    return {"status_code": exc.status_code}

        results = await asyncio.gather(release(), release())
        assert sum(r.get("version") == 1 for r in results) == 1, results
        assert sum(r.get("status_code") == 409 for r in results) == 1, results
        async with factory() as db:
            rows = (await db.scalars(select(SharedSkillVersion).where(SharedSkillVersion.skill_id == skill_id))).all()
            assert len(rows) == 1
        # Separate Python interpreter/connection proves visibility is not an in-process cache.
        code = """import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from enterprise_agent.skills.catalog import catalog
async def main():
    engine = create_async_engine(os.environ['SKILL_TEST_DATABASE_URL'])
    async with async_sessionmaker(engine)() as db:
        loader = await catalog(db, 1)
        item = loader.resolve(os.environ['SKILL_TEST_ID'])
        assert item['version'] == 1 and 'scripts/example.py' in item['package']
    await engine.dispose()
asyncio.run(main())"""
        env = {**os.environ, "SKILL_TEST_ID": f"managed:{skill_id}"}
        completed = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30
        )
        assert completed.returncode == 0, completed.stderr
    finally:
        if skill_id:
            async with factory() as db:
                row = await db.get(SharedSkill, skill_id)
                await db.delete(row)
                await db.commit()
        await engine.dispose()
