// @description Grant test-user package read in RALE integration tests
// @test tests/integration/test_rale_end_to_end.py
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"quilt:ReadPackage",
  resource is Raja::Package
) when {
  resource.packageName == "demo/package-grant" &&
  resource.registry == "raja-poc-registry-{{account}}-{{region}}"
};
