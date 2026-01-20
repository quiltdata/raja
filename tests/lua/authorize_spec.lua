-- Tests for RAJEE authorization logic
-- Run with: busted tests/lua/

describe("S3 Request Parsing", function()
  local parse_s3_request

  before_each(function()
    package.path = package.path .. ";infra/raja_poc/assets/envoy/?.lua"
    local auth = require("authorize_lib")
    parse_s3_request = auth.parse_s3_request
  end)

  describe("parse_s3_request", function()
    it("should parse GET object request", function()
      local result = parse_s3_request("GET", "/bucket/key.txt", {})
      assert.are.equal("S3Object:bucket/key.txt:s3:GetObject", result)
    end)

    it("should parse GET object with nested path", function()
      local result = parse_s3_request("GET", "/bucket/uploads/user123/file.txt", {})
      assert.are.equal("S3Object:bucket/uploads/user123/file.txt:s3:GetObject", result)
    end)

    it("should parse PUT object request", function()
      local result = parse_s3_request("PUT", "/bucket/key.txt", {})
      assert.are.equal("S3Object:bucket/key.txt:s3:PutObject", result)
    end)

    it("should parse DELETE object request", function()
      local result = parse_s3_request("DELETE", "/bucket/key.txt", {})
      assert.are.equal("S3Object:bucket/key.txt:s3:DeleteObject", result)
    end)

    it("should parse HEAD object request", function()
      local result = parse_s3_request("HEAD", "/bucket/key.txt", {})
      assert.are.equal("S3Object:bucket/key.txt:s3:HeadObject", result)
    end)

    it("should parse ListBucket request", function()
      local result = parse_s3_request("GET", "/bucket", { ["list-type"] = "2" })
      assert.are.equal("S3Bucket:bucket:s3:ListBucket", result)
    end)

    it("should parse ListBucketVersions request", function()
      local result = parse_s3_request("GET", "/bucket", { versions = "" })
      assert.are.equal("S3Bucket:bucket:s3:ListBucketVersions", result)
    end)

    it("should parse GetBucketLocation request", function()
      local result = parse_s3_request("GET", "/bucket", { location = "" })
      assert.are.equal("S3Bucket:bucket:s3:GetBucketLocation", result)
    end)

    it("should parse InitiateMultipartUpload", function()
      local result = parse_s3_request("POST", "/bucket/key.txt", { uploads = "" })
      assert.are.equal("S3Object:bucket/key.txt:s3:InitiateMultipartUpload", result)
    end)

    it("should parse UploadPart", function()
      local result = parse_s3_request(
        "PUT",
        "/bucket/key.txt",
        { uploadId = "xyz", partNumber = "1" }
      )
      assert.are.equal("S3Object:bucket/key.txt:s3:UploadPart", result)
    end)

    it("should parse CompleteMultipartUpload", function()
      local result = parse_s3_request("POST", "/bucket/key.txt", { uploadId = "xyz" })
      assert.are.equal("S3Object:bucket/key.txt:s3:CompleteMultipartUpload", result)
    end)

    it("should parse AbortMultipartUpload", function()
      local result = parse_s3_request("DELETE", "/bucket/key.txt", { uploadId = "xyz" })
      assert.are.equal("S3Object:bucket/key.txt:s3:AbortMultipartUpload", result)
    end)

    it("should parse GetObjectVersion", function()
      local result = parse_s3_request("GET", "/bucket/key.txt", { versionId = "xyz" })
      assert.are.equal("S3Object:bucket/key.txt:s3:GetObjectVersion", result)
    end)

    it("should parse DeleteObjectVersion", function()
      local result = parse_s3_request("DELETE", "/bucket/key.txt", { versionId = "xyz" })
      assert.are.equal("S3Object:bucket/key.txt:s3:DeleteObjectVersion", result)
    end)

    it("should parse GetObjectVersionTagging", function()
      local result = parse_s3_request(
        "GET",
        "/bucket/key.txt",
        { versionId = "xyz", tagging = "" }
      )
      assert.are.equal("S3Object:bucket/key.txt:s3:GetObjectVersionTagging", result)
    end)

    it("should parse PutObjectVersionTagging", function()
      local result = parse_s3_request(
        "PUT",
        "/bucket/key.txt",
        { versionId = "xyz", tagging = "" }
      )
      assert.are.equal("S3Object:bucket/key.txt:s3:PutObjectVersionTagging", result)
    end)

    it("should return nil for empty path", function()
      local result = parse_s3_request("GET", "/", {})
      assert.is_nil(result)
    end)
  end)
end)

describe("Authorization Logic", function()
  local authorize

  before_each(function()
    package.path = package.path .. ";infra/raja_poc/assets/envoy/?.lua"
    local auth = require("authorize_lib")
    authorize = auth.authorize
  end)

  describe("authorize", function()
    it("should allow exact match", function()
      local scopes = { "S3Object:bucket/key.txt:s3:GetObject" }
      local allowed, reason = authorize(scopes, "S3Object:bucket/key.txt:s3:GetObject")
      assert.is_true(allowed)
      assert.is_not_nil(string.find(reason, "matched scope"))
    end)

    it("should allow prefix match", function()
      local scopes = { "S3Object:bucket/uploads/:s3:GetObject" }
      local allowed, reason = authorize(scopes, "S3Object:bucket/uploads/file.txt:s3:GetObject")
      assert.is_true(allowed)
      assert.is_not_nil(string.find(reason, "matched scope"))
    end)

    it("should deny different action", function()
      local scopes = { "S3Object:bucket/key.txt:s3:GetObject" }
      local allowed, reason = authorize(scopes, "S3Object:bucket/key.txt:s3:PutObject")
      assert.is_false(allowed)
      assert.is_not_nil(string.find(reason, "no matching scope"))
    end)

    it("should allow HeadObject when GetObject is granted", function()
      local scopes = { "S3Object:bucket/key.txt:s3:GetObject" }
      local allowed = authorize(scopes, "S3Object:bucket/key.txt:s3:HeadObject")
      assert.is_true(allowed)
    end)

    it("should allow multipart when PutObject is granted", function()
      local scopes = { "S3Object:bucket/key.txt:s3:PutObject" }
      local allowed = authorize(scopes, "S3Object:bucket/key.txt:s3:UploadPart")
      assert.is_true(allowed)
    end)

    it("should allow bucket-only scope", function()
      local scopes = { "S3Bucket:bucket:s3:ListBucket" }
      local allowed = authorize(scopes, "S3Bucket:bucket:s3:ListBucket")
      assert.is_true(allowed)
    end)

    it("should deny when no scopes match", function()
      local scopes = { "S3Object:bucket/uploads/:s3:GetObject" }
      local allowed = authorize(scopes, "S3Object:bucket/private/file.txt:s3:GetObject")
      assert.is_false(allowed)
    end)

    it("should deny with empty scopes", function()
      local allowed, reason = authorize({}, "S3Object:bucket/key.txt:s3:GetObject")
      assert.is_false(allowed)
      assert.is_not_nil(string.find(reason, "no scopes"))
    end)
  end)
end)

describe("Query String Parsing", function()
  local parse_query_string

  before_each(function()
    package.path = package.path .. ";infra/raja_poc/assets/envoy/?.lua"
    local auth = require("authorize_lib")
    parse_query_string = auth.parse_query_string
  end)

  it("should parse empty query string", function()
    local result = parse_query_string("")
    assert.are.same({}, result)
  end)

  it("should parse single parameter", function()
    local result = parse_query_string("uploadId=xyz")
    assert.are.equal("xyz", result.uploadId)
  end)

  it("should parse multiple parameters", function()
    local result = parse_query_string("uploadId=xyz&partNumber=1")
    assert.are.equal("xyz", result.uploadId)
    assert.are.equal("1", result.partNumber)
  end)

  it("should parse parameter with empty value", function()
    local result = parse_query_string("uploads=")
    assert.are.equal("", result.uploads)
  end)

  it("should parse parameter without value", function()
    local result = parse_query_string("uploads")
    assert.are.equal("", result.uploads)
  end)
end)
