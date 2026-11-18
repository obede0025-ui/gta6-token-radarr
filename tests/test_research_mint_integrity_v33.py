import unittest
from radar_token_security import LEGACY,evaluate_mint

MINT="A"*44

def account():
    return {"owner":LEGACY,"executable":False,"data":{"parsed":{
      "type":"mint","info":{"mintAuthority":None,"freezeAuthority":None,
                             "decimals":6,"supply":"123456789"}}}}

class TestMintIntegrity(unittest.TestCase):
    def test_valid_basic_fields_clear_with_warning_not_approval(self):
        result=evaluate_mint(MINT,["research"],account())
        self.assertEqual(result["security_gate"],"BASIC_CHECKS_CLEAR")
        self.assertEqual(result["live_trade_approval"],"BLOCK")
        self.assertTrue(result["warnings"])

    def test_reject_executable_account(self):
        data=account();data["executable"]=True
        result=evaluate_mint(MINT,[],data)
        self.assertIn("EXECUTABLE_PROGRAM_ACCOUNT",result["reasons"])
        self.assertEqual(result["security_gate"],"BLOCK")

    def test_invalid_mint_fields_fail_closed(self):
        for value in (-1,20,True,"6"):
            with self.subTest(decimals=value):
                data=account();data["data"]["parsed"]["info"]["decimals"]=value
                result=evaluate_mint(MINT,[],data)
                self.assertIn("MINT_FIELDS_INVALID",result["reasons"])
        for value in (None,-1,"1.2","n/a"):
            with self.subTest(supply=value):
                data=account();data["data"]["parsed"]["info"]["supply"]=value
                result=evaluate_mint(MINT,[],data)
                self.assertIn("MINT_FIELDS_INVALID",result["reasons"])

    def test_invalid_authority_does_not_clear(self):
        data=account()
        data["data"]["parsed"]["info"]["mintAuthority"]="not an address"
        result=evaluate_mint(MINT,[],data)
        self.assertIn("MINT_AUTHORITIES_INVALID",result["reasons"])
        self.assertEqual(result["security_gate"],"BLOCK")

if __name__=="__main__":unittest.main()
