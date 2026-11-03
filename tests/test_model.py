import unittest
from pathlib import Path

from cement_optimizer.model import (
    build_routes,
    load_data,
    nearest_plant_baseline,
    optimize_allocation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TransportationModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plants, cls.dealers = load_data(PROJECT_ROOT / "data")
        cls.routes = build_routes(cls.plants, cls.dealers)

    def test_routes_cover_each_plant_dealer_pair(self):
        self.assertEqual(len(self.routes), len(self.plants) * len(self.dealers))
        self.assertTrue((self.routes["distance_km"] > 0).all())

    def test_baseline_and_optimizer_fulfill_demand_without_exceeding_capacity(self):
        for allocation in (
            nearest_plant_baseline(self.plants, self.dealers, self.routes),
            optimize_allocation(self.plants, self.dealers, self.routes),
        ):
            shipped_by_dealer = allocation.groupby("dealer_id")["quantity_tonnes"].sum()
            shipped_by_plant = allocation.groupby("plant_id")["quantity_tonnes"].sum()
            expected_demand = self.dealers.set_index("dealer_id")["demand_tonnes"]
            capacity = self.plants.set_index("plant_id")["capacity_tonnes"]
            for dealer_id, demand in expected_demand.items():
                self.assertAlmostEqual(shipped_by_dealer[dealer_id], demand)
            for plant_id, shipped in shipped_by_plant.items():
                self.assertLessEqual(shipped, capacity[plant_id] + 1e-6)

    def test_optimized_cost_is_no_higher_than_baseline(self):
        baseline = nearest_plant_baseline(self.plants, self.dealers, self.routes)
        optimized = optimize_allocation(self.plants, self.dealers, self.routes)
        self.assertLessEqual(optimized["cost_inr"].sum(), baseline["cost_inr"].sum() + 0.01)
        self.assertGreater(baseline["cost_inr"].sum(), optimized["cost_inr"].sum())


if __name__ == "__main__":
    unittest.main()
