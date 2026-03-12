// @description Grant demo-user package read for the RALE CLI demo flow
// @owner @ernest
permit(
  principal == Raja::User::"demo-user",
  action == Raja::Action::"quilt:ReadPackage",
  resource is Raja::Package
) when {
  resource.packageName == "demo/package-grant" &&
  resource.registry == "raja-poc-registry-{{account}}-{{region}}"
};
