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


def calculate_ifsc_score(body: ProblemScoreUpsert):
    score = 0
    if body.got_top:
        score = 25 - ((body.attempts_to_top-1) * 0.1)
    elif body.got_bonus:
        score = 15 - ((body.attempts_to_bonus-1) * 0.1)
    return score


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
            ifsc_score=calculate_ifsc_score(body)
        )
        session.add(ps)
        await session.flush()
        await session.commit()
        await session.refresh(ps)

        Response.status_code = status.HTTP_201_CREATED
        return ProblemScoreOut(problem_no=problem_no, **ps.__dict__)

    else:
        existing.attempts_total = body.attempts_total
        existing.got_bonus = body.got_bonus
        existing.got_top = body.got_top
        existing.attempts_to_bonus = body.attempts_to_bonus
        existing.attempts_to_top = body.attempts_to_top
        existing.ifsc_score = calculate_ifsc_score(body)

        await session.flush()
        await session.commit()
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
    # Check that user is registered for that competition and level
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

    # Check that items isn't empty & all problem_nos are distinct
    if not body.items:
        raise HTTPException(status_code=400, detail="No scores submitted.")

    wanted_nos = [item.problem_no for item in body.items]
    if len(wanted_nos) != len(set(wanted_nos)):
        raise HTTPException(status_code=400, detail="Duplicate problem numbers in the submitted batch are not allowed.")

    # Validate the input values before altering DB
    for item in body.items:
        if any(val < 0 for val in [
            item.attempts_total,
            item.attempts_to_bonus,
            item.attempts_to_top
        ]):
            raise HTTPException(
                status_code=400,
                detail=f"Negative value in problem {item.problem_no} not allowed."
            )
        # Add more validation as needed

    # Check that problems exist for the level and comp
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

    problem_by_no: Dict[int, Problem] = {p.problem_no: p for p in problems}
    problem_ids = [problem_by_no[n].id for n in wanted_nos]

    # Load existing ProblemScores for this user & problem set
    existing_scores = (await session.execute(
        select(ProblemScore)
        .where(
            ProblemScore.competition_id == comp_id,
            ProblemScore.user_id == current.id,
            ProblemScore.problem_id.in_(problem_ids),
        )
    )).scalars().all()
    existing_by_pid: Dict[int, ProblemScore] = {ps.problem_id: ps for ps in existing_scores}

    # Upsert (create or update) scores for each item
    results: list[ProblemScoreBulkResult] = []
    # created_updated_info: list[Literal["created", "updated"]] = []  # Optional enhancement

    for item in body.items:
        prob = problem_by_no[item.problem_no]
        ps = existing_by_pid.get(prob.id)
        if ps is None:
            # Create new
            ps = ProblemScore(
                competition_id=comp_id,
                problem_id=prob.id,
                user_id=current.id,
                attempts_total=item.attempts_total,
                got_bonus=item.got_bonus,
                got_top=item.got_top,
                attempts_to_bonus=item.attempts_to_bonus,
                attempts_to_top=item.attempts_to_top,
                ifsc_score=calculate_ifsc_score(item)
            )
            session.add(ps)
            # created_updated_info.append("created")
            existing_by_pid[prob.id] = ps
        else:
            # Update values
            ps.attempts_total = item.attempts_total
            ps.got_bonus = item.got_bonus
            ps.got_top = item.got_top
            ps.attempts_to_bonus = item.attempts_to_bonus
            ps.attempts_to_top = item.attempts_to_top
            ps.ifsc_score = calculate_ifsc_score(item)
            # created_updated_info.append("updated")

        # Defensive type conversion for SQLA 2.x+
        results.append(
            ProblemScoreBulkResult(
                problem_no=item.problem_no,
                score=ProblemScoreOutBulk(
                    attempts_total=int(ps.attempts_total),
                    got_bonus=bool(ps.got_bonus),
                    got_top=bool(ps.got_top),
                    attempts_to_bonus=int(ps.attempts_to_bonus),
                    attempts_to_top=int(ps.attempts_to_top),
                    ifsc_score=float(ps.ifsc_score)
                ),
                # operation=created_updated_info[-1]  # Optional audit enhancement
            )
        )

    await session.flush()
    await session.commit()
    results.sort(key=lambda x: x.problem_no)
    return results


# get batch problem scores
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

    # Get problems for level
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

    # Get scores for user
    scores = (await session.execute(
        select(ProblemScore)
        .where(
            ProblemScore.competition_id == comp_id,
            ProblemScore.user_id == current.id,
            ProblemScore.problem_id.in_(problem_by_id.keys()),
        )
    )).scalars().all()

    results: list[ProblemScoreBulkResult] = []

    if scores:
        for ps in scores:
            prob = problem_by_id[ps.problem_id]
            results.append(
                ProblemScoreBulkResult(
                    problem_no=prob.problem_no,
                    score=ProblemScoreOutBulk(
                        attempts_total=ps.attempts_total,
                        got_bonus=ps.got_bonus,
                        got_top=ps.got_top,
                        attempts_to_bonus=ps.attempts_to_bonus,
                        attempts_to_top=ps.attempts_to_top,
                        ifsc_score=ps.ifsc_score
                    ),
                )
            )
    else:
        # Return 0-attempt score objects if no scores exist yet
        for prob in problems:
            results.append(
                ProblemScoreBulkResult(
                    problem_no=prob.problem_no,
                    score=ProblemScoreOutBulk(
                        attempts_total=0,
                        got_bonus=False,
                        got_top=False,
                        attempts_to_bonus=0,
                        attempts_to_top=0,
                        ifsc_score=0
                    ),
                )
            )

    results.sort(key=lambda x: x.problem_no)
    return results
