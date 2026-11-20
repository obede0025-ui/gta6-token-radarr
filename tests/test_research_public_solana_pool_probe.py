"""Offline unit tests for public RPC verification probe; no network calls."""
import unittest
from research_public_solana_pool_probe import CASES, evaluate_case, probe


class FakeSession:
    def __init__(self):
        self.methods = []

    def post(self, endpoint, json, timeout):
        self.methods.append(json["method"])
        class Response:
            def raise_for_status(self):
                return None
            def __init__(self, result):
                self.result = result
            def json(self):
                return {"result":self.result}
        if json["method"] == "getMultipleAccounts":
            return Response({"value":[
                {"owner":"ExampleProgram", "executable":False}
                for _ in CASES
            ]})
        if json["method"] == "getSignaturesForAddress":
            return Response([])
        raise AssertionError("Unexpected RPC method")


class ProbeTests(unittest.TestCase):
    def test_no_history_never_confirms_liquidity(self):
        result = evaluate_case(CASES[0], {"owner":"TestProgram"}, [])
        self.assertEqual(result["result"],"UNVERIFIED")
        self.assertFalse(result["historical_reserves_verified"])
        self.assertFalse(result["signature_sample_covers_target"])

    def test_read_only_calls_and_three_cases(self):
        session = FakeSession()
        result = probe("https://example.invalid/rpc", session, 10)
        self.assertEqual(session.methods,
            ["getMultipleAccounts"] + ["getSignaturesForAddress"] * 3)
        self.assertEqual(len(result["cases"]), 3)
        self.assertTrue(all(c["result"] == "UNVERIFIED" for c in result["cases"]))
        self.assertEqual(result["database_writes"],0)
        self.assertEqual(result["trades_executed"],0)


if __name__ == "__main__":
    unittest.main()
