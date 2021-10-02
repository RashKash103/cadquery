from typing import Tuple, Union, Any, Callable, List, Optional, Iterable, Dict, Sequence
from typing_extensions import Literal
from nptyping import NDArray as Array
from itertools import accumulate, chain
from math import sin, cos, pi

from numpy import array, full, inf, sign
from numpy.linalg import norm
import nlopt

from OCP.gp import gp_Vec2d

from .shapes import Geoms

NoneType = type(None)

SegmentDOF = Tuple[float, float, float, float]  # p1 p2
ArcDOF = Tuple[float, float, float, float, float]  # p r a da
DOF = Union[SegmentDOF, ArcDOF]

ConstraintKind = Literal[
    "Fixed",
    "Coincident",
    "Angle",
    "Length",
    "Distance",
    "Radius",
    "Orientation",
    "ArcAngle",
]

ConstraintInvariants = {  # (arity, geometry types, param type)
    "Fixed": (1, ("CIRCLE", "LINE"), NoneType),
    "Coincident": (2, ("CIRCLE", "LINE"), NoneType),
    "Angle": (2, ("CIRCLE", "LINE"), float),
    "Length": (1, ("CIRCLE", "LINE"), float),
    "Distance": (2, ("CIRCLE", "LINE"), Tuple[float, float, float]),
    "Radius": (1, ("CIRCLE",), float),
    "Orientation": (1, ("LINE",), Tuple[float, float]),
    "ArcAngle": (1, ("CIRCLE",), float),
}

Constraint = Tuple[Tuple[int, Optional[int]], ConstraintKind, Optional[Any]]

DIFF_EPS = 1e-10
TOL = 1e-9
MAXITER = 0


def invalid_args(*t):

    return ValueError("Invalid argument types {t}")


def arc_first(x):

    return array((x[0] + x[2] * sin(x[3]), x[1] + x[2] * cos(x[3])))


def arc_last(x):

    return array((x[0] + x[2] * sin(x[3] + x[4]), x[1] + x[2] * cos(x[3] + x[4])))


def arc_point(x, val):

    if val is None:
        rv = x[:2]
    else:
        a = x[3] + val * x[4]
        rv = array((x[0] + x[2] * sin(a), x[1] + x[2] * cos(a)))

    return rv


def line_point(x, val):

    return x[:2] + val * x[2:]


def arc_first_tangent(x):

    return gp_Vec2d(sign(x[4]) * cos(x[3]), -sign(x[4]) * sin(x[3]))


def arc_last_tangent(x):

    return gp_Vec2d(sign(x[4]) * cos(x[3] + x[4]), -sign(x[4]) * sin(x[3] + x[4]))


def fixed_cost(x, t, val):

    return norm(x - val)


def coincident_cost(x1, t1, x2, t2, val):

    if t1 == "LINE" and t2 == "LINE":
        v1 = x1[2:]
        v2 = x2[:2]
    elif t1 == "LINE" and t2 == "CIRCLE":
        v1 = x1[2:]
        v2 = arc_first(x2)
    elif t1 == "CIRCLE" and t2 == "LINE":
        v1 = arc_last(x1)
        v2 = x2[:2]
    elif t1 == "CIRCLE" and t2 == "CIRCLE":
        v1 = arc_last(x1)
        v2 = arc_first(x2)
    else:
        raise invalid_args(t1, t2)

    return norm(v1 - v2)


def angle_cost(x1, t1, x2, t2, val):

    if t1 == "LINE" and t2 == "LINE":
        v1 = gp_Vec2d(*(x1[2:] - x1[:2]))
        v2 = gp_Vec2d(*(x2[2:] - x2[:2]))
    elif t1 == "LINE" and t2 == "CIRCLE":
        v1 = gp_Vec2d(*(x1[2:] - x1[:2]))
        v2 = arc_first_tangent(x2)
    elif t1 == "CIRCLE" and t2 == "LINE":
        v1 = arc_last_tangent(x1)
        v2 = gp_Vec2d(*(x2[2:] - x2[:2]))
    elif t1 == "CIRCLE" and t2 == "CIRCLE":
        v1 = arc_last_tangent(x1)
        v2 = arc_first_tangent(x2)
    else:
        raise invalid_args(t1, t2)

    return v2.Angle(v1) - val


def length_cost(x, t, val):

    rv = 0

    if t == "LINE":
        rv = norm(x[2:] - x[:2]) - val
    elif t == "CIRCLE":
        rv = norm(x[2] * (x[4] - x[3])) - val
    else:
        raise invalid_args(t)

    return rv


def distance_cost(x1, t1, x2, t2, val):

    val1, val2, d = val

    if t1 == "LINE" and t2 == "LINE":
        v1 = line_point(x1, val1)
        v2 = line_point(x2, val2)
    elif t1 == "LINE" and t2 == "CIRCLE":
        v1 = line_point(x1, val1)
        v2 = arc_point(x2, val2)
    elif t1 == "CIRCLE" and t2 == "LINE":
        v1 = arc_point(x1, val1)
        v2 = line_point(x2, val2)
    elif t1 == "CIRCLE" and t2 == "CIRCLE":
        v1 = arc_point(x1, val1)
        v2 = arc_point(x2, val2)
    else:
        raise invalid_args(t1, t2)

    return norm(v1 - v2) - d


def radius_cost(x, t, val):

    if t == "CIRCLE":
        rv = x[2] - val
    else:
        raise invalid_args(t)

    return rv


def orientation_cost(x, t, val):

    if t == "LINE":
        rv = gp_Vec2d(*(x[2:] - x[:2])).Angle(gp_Vec2d(*val))
    else:
        raise invalid_args(t)

    return rv


def arc_angle_cost(x, t, val):

    if t == "CIRCLE":
        rv = norm(x[4] - x[3]) - val
    else:
        raise invalid_args(t)

    return rv


# dicitonary of individual constraint cost functions
costs: Dict[str, Callable[..., float]] = dict(
    Fixed=fixed_cost,
    Coincident=coincident_cost,
    Angle=angle_cost,
    Length=length_cost,
    Distance=distance_cost,
    Radius=radius_cost,
    Orientation=orientation_cost,
    ArcAngle=arc_angle_cost,
)


class SketchConstraintSolver(object):

    entities: List[DOF]
    constraints: List[Constraint]
    geoms: List[Geoms]
    ne: int
    nc: int
    ixs: List[int]

    def __init__(
        self,
        entities: Iterable[DOF],
        constraints: Iterable[Constraint],
        geoms: Iterable[Geoms],
    ):

        self.entities = list(entities)
        self.constraints = list(constraints)
        self.geoms = list(geoms)

        self.ne = len(self.entities)
        self.nc = len(self.constraints)

        # validate and transfrom constraints

        # indices of x corresponding to the entities
        self.ixs = [0] + list(accumulate(len(e) for e in self.entities))

    def _cost(
        self,
    ) -> Tuple[
        Callable[[Array[(Any,), float]], float],
        Callable[[Array[(Any,), float], Array[(Any,), float]], None],
        Array[(Any,), float],
        Array[(Any,), float],
    ]:

        ixs = self.ixs
        constraints = self.constraints
        geoms = self.geoms

        def f(x) -> float:
            """
            Cost function to be minimized
            """

            rv = 0.0

            for i, ((e1, e2), kind, val) in enumerate(constraints):

                cost = costs[kind]

                # build arguments for the specific constraint
                args = [x[ixs[e1] : ixs[e1 + 1]], geoms[e1]]
                if e2 is not None:
                    args += [x[ixs[e2] : ixs[e2 + 1]], geoms[e2]]

                # evaluate
                rv += cost(*args, val) ** 2

            return rv

        def grad(x, rv) -> None:
            """
            Gradient of the cost function
            """

            rv[:] = 0

            for i, ((e1, e2), kind, val) in enumerate(constraints):

                cost = costs[kind]

                # build arguments for the specific constraint
                x1 = x[ixs[e1] : ixs[e1 + 1]]
                args = [x1.copy(), geoms[e1]]
                if e2 is not None:
                    x2 = x[ixs[e2] : ixs[e2 + 1]]
                    args += [x2.copy(), geoms[e2]]

                # evaluate
                tmp = cost(*args, val)

                for j, k in enumerate(range(ixs[e1], ixs[e1 + 1])):
                    args[0][j] += DIFF_EPS
                    tmp1 = cost(*args, val)
                    rv[k] += 2 * tmp * (tmp1 - tmp) / DIFF_EPS
                    args[0][j] = x1[j]

                if e2 is not None:
                    for j, k in enumerate(range(ixs[e2], ixs[e2 + 1])):
                        args[2][j] += DIFF_EPS
                        tmp2 = cost(*args, val)
                        rv[k] += 2 * tmp * (tmp2 - tmp) / DIFF_EPS
                        args[2][j] = x2[j]

        # generate lower and upper bounds for optimization
        lb = full(ixs[-1], -inf)
        ub = full(ixs[-1], +inf)

        for i, g in enumerate(geoms):
            if g == "CIRCLE":
                lb[ixs[i] + 2] = 0  # lower bound for radius

        return f, grad, lb, ub

    def solve(self) -> Tuple[Sequence[Sequence[float]], Dict[str, Any]]:

        x0 = array(list(chain.from_iterable(self.entities))).ravel()
        f, grad, lb, ub = self._cost()

        def func(x, g):

            if g.size > 0:
                grad(x, g)

            return f(x)

        opt = nlopt.opt(nlopt.LD_SLSQP, len(x0))
        opt.set_min_objective(func)
        opt.set_lower_bounds(lb)
        opt.set_upper_bounds(ub)

        opt.set_ftol_abs(0)
        opt.set_ftol_rel(0)
        opt.set_xtol_rel(TOL)
        opt.set_xtol_abs(TOL * 1e-3)
        opt.set_maxeval(MAXITER)

        x = opt.optimize(x0)
        status = {
            "entities": self.entities,
            "cost": opt.last_optimum_value(),
            "iters": opt.get_numevals(),
            "status": opt.last_optimize_result(),
        }

        ixs = self.ixs

        return [x[i1:i2] for i1, i2 in zip(ixs, ixs[1:])], status
