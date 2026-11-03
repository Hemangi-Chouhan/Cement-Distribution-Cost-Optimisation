"""Data loading, baseline allocation, and linear-programming optimization."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pulp

PLANT_COLUMNS = {
    "plant_id",
    "plant_name",
    "city",
    "latitude",
    "longitude",
    "capacity_tonnes",
}
DEALER_COLUMNS = {
    "dealer_id",
    "dealer_name",
    "city",
    "latitude",
    "longitude",
    "demand_tonnes",
}
EARTH_RADIUS_KM = 6371.0
ROAD_DISTANCE_FACTOR = 1.25
TRANSPORT_RATE_INR_PER_TONNE_KM = 3.0


def load_data(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load and validate plant and dealer CSV files."""
    plants = pd.read_csv(data_dir / "plants.csv")
    dealers = pd.read_csv(data_dir / "dealers.csv")

    for label, frame, required, id_column, quantity_column in (
        ("plants", plants, PLANT_COLUMNS, "plant_id", "capacity_tonnes"),
        ("dealers", dealers, DEALER_COLUMNS, "dealer_id", "demand_tonnes"),
    ):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{label}.csv is missing columns: {', '.join(sorted(missing))}")
        if frame.empty:
            raise ValueError(f"{label}.csv must contain at least one row")
        if frame[id_column].isna().any() or frame[id_column].duplicated().any():
            raise ValueError(f"{label}.csv must contain unique, non-empty {id_column} values")
        if frame[quantity_column].isna().any() or (frame[quantity_column] <= 0).any():
            raise ValueError(f"{label}.csv {quantity_column} values must be positive")
        for coordinate in ("latitude", "longitude"):
            if frame[coordinate].isna().any():
                raise ValueError(f"{label}.csv {coordinate} values must not be empty")

    if plants["capacity_tonnes"].sum() < dealers["demand_tonnes"].sum():
        raise ValueError("Total plant capacity is less than total dealer demand")

    return plants, dealers


def road_distance_km(plant: pd.Series, dealer: pd.Series) -> int:
    """Estimate road distance from coordinates using a configurable detour factor."""
    lat1, lon1 = math.radians(plant["latitude"]), math.radians(plant["longitude"])
    lat2, lon2 = math.radians(dealer["latitude"]), math.radians(dealer["longitude"])
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    straight_line_km = 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(haversine))
    return max(1, round(straight_line_km * ROAD_DISTANCE_FACTOR))


def build_routes(plants: pd.DataFrame, dealers: pd.DataFrame) -> pd.DataFrame:
    """Build every plant-dealer route and its assumed per-tonne transport cost."""
    routes = []
    for _, plant in plants.iterrows():
        for _, dealer in dealers.iterrows():
            distance = road_distance_km(plant, dealer)
            routes.append(
                {
                    "plant_id": plant["plant_id"],
                    "dealer_id": dealer["dealer_id"],
                    "distance_km": distance,
                    "cost_per_tonne_inr": distance * TRANSPORT_RATE_INR_PER_TONNE_KM,
                }
            )
    return pd.DataFrame(routes)


def _format_allocation(
    plants: pd.DataFrame,
    dealers: pd.DataFrame,
    routes: pd.DataFrame,
    quantities: dict[tuple[str, str], float],
) -> pd.DataFrame:
    plant_names = plants.set_index("plant_id")["plant_name"].to_dict()
    dealer_names = dealers.set_index("dealer_id")["dealer_name"].to_dict()
    route_costs = routes.set_index(["plant_id", "dealer_id"])
    rows = []
    for (plant_id, dealer_id), quantity in quantities.items():
        if quantity <= 1e-6:
            continue
        route = route_costs.loc[(plant_id, dealer_id)]
        rows.append(
            {
                "plant_id": plant_id,
                "plant_name": plant_names[plant_id],
                "dealer_id": dealer_id,
                "dealer_name": dealer_names[dealer_id],
                "quantity_tonnes": round(quantity, 3),
                "distance_km": int(route["distance_km"]),
                "cost_inr": round(quantity * route["cost_per_tonne_inr"], 2),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "plant_id",
            "plant_name",
            "dealer_id",
            "dealer_name",
            "quantity_tonnes",
            "distance_km",
            "cost_inr",
        ],
    )


def nearest_plant_baseline(
    plants: pd.DataFrame, dealers: pd.DataFrame, routes: pd.DataFrame
) -> pd.DataFrame:
    """Allocate each dealer from the nearest plant with capacity remaining."""
    remaining = plants.set_index("plant_id")["capacity_tonnes"].to_dict()
    quantities: dict[tuple[str, str], float] = {}

    for _, dealer in dealers.iterrows():
        demand_left = float(dealer["demand_tonnes"])
        dealer_routes = routes[routes["dealer_id"] == dealer["dealer_id"]].sort_values(
            ["distance_km", "plant_id"]
        )
        for _, route in dealer_routes.iterrows():
            plant_id = route["plant_id"]
            shipment = min(demand_left, remaining[plant_id])
            if shipment > 0:
                quantities[(plant_id, dealer["dealer_id"])] = shipment
                remaining[plant_id] -= shipment
                demand_left -= shipment
            if demand_left <= 1e-6:
                break
        if demand_left > 1e-6:
            raise ValueError(f"Insufficient remaining capacity for {dealer['dealer_name']}")

    return _format_allocation(plants, dealers, routes, quantities)


def optimize_allocation(
    plants: pd.DataFrame, dealers: pd.DataFrame, routes: pd.DataFrame
) -> pd.DataFrame:
    """Solve the capacitated transportation problem as a linear program."""
    plant_ids = plants["plant_id"].tolist()
    dealer_ids = dealers["dealer_id"].tolist()
    capacities = plants.set_index("plant_id")["capacity_tonnes"].to_dict()
    demands = dealers.set_index("dealer_id")["demand_tonnes"].to_dict()
    costs = routes.set_index(["plant_id", "dealer_id"])["cost_per_tonne_inr"].to_dict()

    problem = pulp.LpProblem("Cement_Distribution_Cost_Minimization", pulp.LpMinimize)
    shipped = pulp.LpVariable.dicts(
        "tonnes", (plant_ids, dealer_ids), lowBound=0, cat=pulp.LpContinuous
    )
    problem += pulp.lpSum(
        costs[(plant_id, dealer_id)] * shipped[plant_id][dealer_id]
        for plant_id in plant_ids
        for dealer_id in dealer_ids
    )
    for plant_id in plant_ids:
        problem += (
            pulp.lpSum(shipped[plant_id][dealer_id] for dealer_id in dealer_ids)
            <= capacities[plant_id],
            f"capacity_{plant_id}",
        )
    for dealer_id in dealer_ids:
        problem += (
            pulp.lpSum(shipped[plant_id][dealer_id] for plant_id in plant_ids)
            == demands[dealer_id],
            f"demand_{dealer_id}",
        )

    problem.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[problem.status] != "Optimal":
        raise RuntimeError(f"Optimizer did not find an optimal solution: {pulp.LpStatus[problem.status]}")

    quantities = {
        (plant_id, dealer_id): float(pulp.value(shipped[plant_id][dealer_id]) or 0)
        for plant_id in plant_ids
        for dealer_id in dealer_ids
    }
    return _format_allocation(plants, dealers, routes, quantities)
