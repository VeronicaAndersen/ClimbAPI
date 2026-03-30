from typing import Annotated, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Problem, Registration, ProblemScore
from schema.problem_score import (
    ProblemScoreUpsert,
    ProblemScoreOut,
    ProblemScoreBulkResult,
    ProblemScoreBulkRequest,
    ProblemScoreOutBulk,
)
from security.deps import CurrentUser

router = APIRouter(prefix="/competitions", tags=["scores"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def calculate_ifsc_score(body: ProblemScoreUpsert) -> float:
    if body.got_top:
        return 25 - ((body.attempts_to_top - 1) * 0.1)
    if body.got_bonus:
        return 15 - ((body.attempts_to_bonus - 1) * 0.1)
    return 0.0


async def _require_registration(
    session: AsyncSession, comp_id: int, user_id: int, level: int
) -> None:
    reg = await session.scalar(
        select(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == user_id,
        )
    )
    if not reg:
        raise HTTPException(status_code=403, detail="Not registered for this competition")
    if reg.level != level:
        raise HTTPException(status_code=403, detail="Registered for a different level")


def _build_score_result(problem_no: int, ps: ProblemScore) -> ProblemScoreBulkResult:
    return ProblemScoreBulkResult(
        problem_no=problem_no,
        score=ProblemScoreOutBulk(
            attempts_total=ps.attempts_total,
            got_bonus=ps.got_bonus,
            got_top=ps.got_top,
            attempts_to_bonus=ps.attempts_to_bonus,
            attempts_to_top=ps.attempts_to_top,
            ifsc_score=ps.ifsc_score,
        ),
    )


def _apply_score_fields(ps: ProblemScore, body: ProblemScoreUpsert) -> None:
    ps.attempts_total = body.attempts_total
    ps.got_bonus = body.got_bonus
    ps.got_top = body.got_top
    ps.attempts_to_bonus = body.attempts_to_bonus
    ps.attempts_to_top = body.attempts_to_top
    ps.ifsc_score = calculate_ifsc_score(body)


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

    await _require_registration(session, comp_id, current.id, level_no)

    ps = await session.scalar(
        select(ProblemScore).where(
            ProblemScore.competition_id == comp_id,
            ProblemScore.problem_id == problem.id,
            ProblemScore.user_id == current.id,
        )
    )

    if ps is None:
        ps = ProblemScore(
            competition_id=comp_id,
            problem_id=problem.id,
            user_id=current.id,
        )
        session.add(ps)

    _apply_score_fields(ps, body)
    await session.flush()
    await session.commit()
    await session.refresh(ps)
    return ProblemScoreOut(problem_no=problem_no, **ps.__dict__)


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
    await _require_registration(session, comp_id, current.id, level)

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
        missing = sorted(set(wanted_nos) - {p.problem_no for p in problems})
        raise HTTPException(status_code=404, detail=f"Problems not found: {missing}")

    problem_by_no: Dict[int, Problem] = {p.problem_no: p for p in problems}
    problem_ids = [p.id for p in problems]

    existing_scores = (await session.execute(
        select(ProblemScore)
        .where(
            ProblemScore.competition_id == comp_id,
            ProblemScore.user_id == current.id,
            ProblemScore.problem_id.in_(problem_ids),
        )
    )).scalars().all()
    existing_by_pid: Dict[int, ProblemScore] = {ps.problem_id: ps for ps in existing_scores}

    results: list[ProblemScoreBulkResult] = []

    for item in body.items:
        prob = problem_by_no[item.problem_no]
        ps = existing_by_pid.get(prob.id)
        if ps is None:
            ps = ProblemScore(
                competition_id=comp_id,
                problem_id=prob.id,
                user_id=current.id,
            )
            session.add(ps)
            existing_by_pid[prob.id] = ps

        _apply_score_fields(ps, item)
        results.append(_build_score_result(item.problem_no, ps))

    await session.flush()
    await session.commit()
    results.sort(key=lambda x: x.problem_no)
    return results


@router.get(
    "/{comp_id}/level/{level}/scores/batch",
    response_model=list[ProblemScoreBulkResult],
    status_code=status.HTTP_200_OK,
)
async def get_problem_scores_batch(
        comp_id: int,
        level: int,
        session: SessionDep,
        current: CurrentUser,
):
    await _require_registration(session, comp_id, current.id, level)

    problems = (await session.execute(
        select(Problem)
        .where(
            Problem.competition_id == comp_id,
            Problem.level_no == level,
        )
    )).scalars().all()

    if not problems:
        raise HTTPException(status_code=404, detail="No problems found for this level")

    problem_by_id: Dict[int, Problem] = {p.id: p for p in problems}

    scores = (await session.execute(
        select(ProblemScore)
        .where(
            ProblemScore.competition_id == comp_id,
            ProblemScore.user_id == current.id,
            ProblemScore.problem_id.in_(problem_by_id.keys()),
        )
    )).scalars().all()

    if scores:
        results = [_build_score_result(problem_by_id[ps.problem_id].problem_no, ps) for ps in scores]
    else:
        # Return zero-attempt objects if no scores exist yet
        results = [
            ProblemScoreBulkResult(
                problem_no=prob.problem_no,
                score=ProblemScoreOutBulk(
                    attempts_total=0,
                    got_bonus=False,
                    got_top=False,
                    attempts_to_bonus=0,
                    attempts_to_top=0,
                    ifsc_score=0.0,
                ),
            )
            for prob in problems
        ]

    results.sort(key=lambda x: x.problem_no)
    return results
