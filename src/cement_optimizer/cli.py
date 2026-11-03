"""Command-line entry point for the cement distribution optimizer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from cement_optimizer.model import (
    build_routes,
    load_data,
    nearest_plant_baseline,
    optimize_allocation,
)


def write_route_map(
    plants: pd.DataFrame,
    dealers: pd.DataFrame,
    allocation: pd.DataFrame,
    output_path: Path,
) -> None:
    """Create an interactive Folium map of the optimized shipment routes."""
    import folium

    locations = pd.concat(
        [
            plants[["latitude", "longitude"]],
            dealers[["latitude", "longitude"]],
        ]
    )
    center = [locations["latitude"].mean(), locations["longitude"].mean()]
    route_map = folium.Map(location=center, zoom_start=5, control_scale=True)

    for _, plant in plants.iterrows():
        folium.Marker(
            [plant["latitude"], plant["longitude"]],
            tooltip=f'{plant["plant_name"]} ({plant["capacity_tonnes"]:,.0f} t capacity)',
            icon=folium.Icon(color="darkblue", icon="industry", prefix="fa"),
        ).add_to(route_map)
    for _, dealer in dealers.iterrows():
        folium.Marker(
            [dealer["latitude"], dealer["longitude"]],
            tooltip=f'{dealer["dealer_name"]} ({dealer["demand_tonnes"]:,.0f} t demand)',
            icon=folium.Icon(color="green", icon="shopping-cart", prefix="fa"),
        ).add_to(route_map)

    plant_locations = plants.set_index("plant_id")[["latitude", "longitude"]]
    dealer_locations = dealers.set_index("dealer_id")[["latitude", "longitude"]]
    for _, shipment in allocation.iterrows():
        start = plant_locations.loc[shipment["plant_id"]]
        end = dealer_locations.loc[shipment["dealer_id"]]
        folium.PolyLine(
            [(start["latitude"], start["longitude"]), (end["latitude"], end["longitude"])],
            tooltip=(
                f'{shipment["plant_name"]} → {shipment["dealer_name"]}: '
                f'{shipment["quantity_tonnes"]:,.0f} t, '
                f'{shipment["distance_km"]} km'
            ),
            weight=2 + min(shipment["quantity_tonnes"] / 1500, 7),
            color="darkred",
            opacity=0.65,
        ).add_to(route_map)

    route_map.save(str(output_path))


def run(data_dir: Path, output_dir: Path, make_map: bool = True) -> dict[str, float]:
    plants, dealers = load_data(data_dir)
    routes = build_routes(plants, dealers)
    baseline = nearest_plant_baseline(plants, dealers, routes)
    optimized = optimize_allocation(plants, dealers, routes)

    output_dir.mkdir(parents=True, exist_ok=True)
    routes.to_csv(output_dir / "route_distances.csv", index=False)
    baseline.to_csv(output_dir / "baseline_allocation.csv", index=False)
    optimized.to_csv(output_dir / "optimized_allocation.csv", index=False)

    baseline_cost = float(baseline["cost_inr"].sum())
    optimized_cost = float(optimized["cost_inr"].sum())
    savings = baseline_cost - optimized_cost
    results = {
        "baseline_cost_inr": round(baseline_cost, 2),
        "optimized_cost_inr": round(optimized_cost, 2),
        "savings_inr": round(savings, 2),
        "savings_percent": round(savings / baseline_cost * 100, 2) if baseline_cost else 0.0,
        "total_demand_tonnes": int(dealers["demand_tonnes"].sum()),
        "total_capacity_tonnes": int(plants["capacity_tonnes"].sum()),
        "baseline_routes": int(len(baseline)),
        "optimized_routes": int(len(optimized)),
    }
    (output_dir / "summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    if make_map:
        write_route_map(plants, dealers, optimized, output_dir / "routes_map.html")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimize cement transport cost from plants to dealers.")
    project_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--data-dir", type=Path, default=project_root / "data")
    parser.add_argument("--output-dir", type=Path, default=project_root / "outputs")
    parser.add_argument("--no-map", action="store_true", help="Skip creating the interactive HTML map.")
    args = parser.parse_args()

    results = run(args.data_dir, args.output_dir, make_map=not args.no_map)
    print("Cement distribution cost comparison")
    print(f'  Nearest-plant baseline: INR {results["baseline_cost_inr"]:,.0f}')
    print(f'  Optimized allocation:   INR {results["optimized_cost_inr"]:,.0f}')
    print(f'  Estimated savings:     INR {results["savings_inr"]:,.0f} ({results["savings_percent"]:.2f}%)')
    print(f'  Results written to: {args.output_dir.resolve()}')


if __name__ == "__main__":
    main()
