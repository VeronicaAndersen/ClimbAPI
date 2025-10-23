from typing import Annotated, Dict

from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Problem, Registration, ProblemScore
from schema.problem_score import ProblemScoreUpsert, ProblemScoreOut, ProblemScoreBulkResult, ProblemScoreBulkRequest, \
    ProblemScoreOutBulk
from security.deps import CurrentUser

router = APIRouter(prefix="/competitions", tags=["scores"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.put(
    "/{comp_id}/level/{level_no}/problems/{problem_no}/score",
    response_model=ProblemScoreOut,
    status_code=status.HTTP_200_OK,
)
async def upsert_problem_score(
        comp_id: int,
        level_no: int,
        problem_no: int,
        body: ProblemScoreUpsert,
        session: SessionDep,
        current: CurrentUser,
):
    problem = await session.scalar(
        select(Problem).where(
            Problem.competition_id == comp_id,
            Problem.level_no == level_no,
            Problem.problem_no == problem_no,
        )
    )
    if not problem:
        raise HTTPException(status_code=404, detail="Problem not found")

    reg = await session.scalar(
        select(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == current.id,
        )
    )
    if not reg:
        raise HTTPException(status_code=403, detail="Not registered for this competition")
    if reg.level != level_no:
        raise HTTPException(status_code=403, detail="Registered for a different level")

    existing = await session.scalar(
        select(ProblemScore).where(
            ProblemScore.competition_id == comp_id,
            ProblemScore.problem_id == problem.id,
            ProblemScore.user_id == current.id,
        )
    )

    if not existing:
        # Create new
        ps = ProblemScore(
            competition_id=comp_id,
            problem_id=problem.id,
            user_id=current.id,
            attempts_total=body.attempts_total,
            got_bonus=body.got_bonus,
            got_top=body.got_top,
            attempts_to_bonus=body.attempts_to_bonus,
            attempts_to_top=body.attempts_to_top,
        )
        session.add(ps)
        await session.flush()
        await session.refresh(ps)

        Response.status_code = status.HTTP_201_CREATED
        return ProblemScoreOut(problem_no=problem_no, **ps.__dict__)

    else:
        existing.attempts_total = body.attempts_total
        existing.got_bonus = body.got_bonus
        existing.got_top = body.got_top
        existing.attempts_to_bonus = body.attempts_to_bonus
        existing.attempts_to_top = body.attempts_to_top

        await session.flush()
        await session.refresh(existing)
        return ProblemScoreOut(problem_no=problem_no, **existing.__dict__)


@router.put(
    "/{comp_id}/level/{level}/scores/batch",
    response_model=list[ProblemScoreBulkResult],
    status_code=status.HTTP_200_OK,
)
async def upsert_problem_scores_batch(
        comp_id: int,
        level: int,
        body: ProblemScoreBulkRequest,
        session: SessionDep,
        current: CurrentUser,
):
    # Check that user is registered
    reg = await session.scalar(
        select(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == current.id,
        )
    )
    if not reg:
        raise HTTPException(status_code=403, detail="Not registered for this competition")
    if reg.level != level:
        raise HTTPException(status_code=403, detail="Registered for a different level")

    # Check that problems exist
    wanted_nos = [item.problem_no for item in body.items]
    problems = (await session.execute(
        select(Problem)
        .where(
            Problem.competition_id == comp_id,
            Problem.level_no == level,
            Problem.problem_no.in_(wanted_nos),
        )
    )).scalars().all()
    if len(problems) != len(wanted_nos):
        have = {p.problem_no for p in problems}
        missing = sorted(set(wanted_nos) - have)
        raise HTTPException(status_code=404, detail=f"Problems not found: {missing}")

    # Load existing
    problem_by_no: Dict[int, Problem] = {p.problem_no: p for p in problems}

    problem_ids = [problem_by_no[n].id for n in wanted_nos]
    existing_scores = (await session.execute(
        select(ProblemScore)
        .where(
            ProblemScore.competition_id == comp_id,
            ProblemScore.user_id == current.id,
            ProblemScore.problem_id.in_(problem_ids),
        )
    )).scalars().all()
    existing_by_pid: Dict[int, ProblemScore] = {ps.problem_id: ps for ps in existing_scores}

    # Upsert
    results: list[ProblemScoreBulkResult] = []
    for item in body.items:
        prob = problem_by_no[item.problem_no]
        ps = existing_by_pid.get(prob.id)
        if ps is None:
            ps = ProblemScore(
                competition_id=comp_id,
                problem_id=prob.id,
                user_id=current.id,
                attempts_total=item.attempts_total,
                got_bonus=item.got_bonus,
                got_top=item.got_top,
                attempts_to_bonus=item.attempts_to_bonus,
                attempts_to_top=item.attempts_to_top,
            )
            session.add(ps)
            existing_by_pid[prob.id] = ps
        else:
            ps.attempts_total = item.attempts_total
            ps.got_bonus = item.got_bonus
            ps.got_top = item.got_top
            ps.attempts_to_bonus = item.attempts_to_bonus
            ps.attempts_to_top = item.attempts_to_top

        results.append(
            ProblemScoreBulkResult(
                problem_no=item.problem_no,
                score=ProblemScoreOutBulk(
                    attempts_total=ps.attempts_total,
                    got_bonus=ps.got_bonus,
                    got_top=ps.got_top,
                    attempts_to_bonus=ps.attempts_to_bonus,
                    attempts_to_top=ps.attempts_to_top,
                ),
            )
        )

    await session.flush()
    # (optional) sort by problem_no for deterministic order
    results.sort(key=lambda x: x.problem_no)
    return results
