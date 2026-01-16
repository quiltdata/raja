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
      assert.are.equal("s3:GetObject/bucket/key.txt", result)
    end)

    it("should parse GET object with nested path", function()
      local result = parse_s3_request("GET", "/bucket/uploads/user123/file.txt", {})
      assert.are.equal("s3:GetObject/bucket/uploads/user123/file.txt", result)
    end)

    it("should parse PUT object request", function()
      local result = parse_s3_request("PUT", "/bucket/key.txt", {})
      assert.are.equal("s3:PutObject/bucket/key.txt", result)
    end)

    it("should parse DELETE object request", function()
      local result = parse_s3_request("DELETE", "/bucket/key.txt", {})
      assert.are.equal("s3:DeleteObject/bucket/key.txt", result)
    end)

    it("should parse HEAD object request", function()
      local result = parse_s3_request("HEAD", "/bucket/key.txt", {})
      assert.are.equal("s3:HeadObject/bucket/key.txt", result)
    end)

    it("should parse ListBucket request", function()
      local result = parse_s3_request("GET", "/bucket/", {})
      assert.are.equal("s3:ListBucket/bucket/", result)
    end)

    it("should parse InitiateMultipartUpload", function()
      local result = parse_s3_request("POST", "/bucket/key.txt", { uploads = "" })
      assert.are.equal("s3:InitiateMultipartUpload/bucket/key.txt", result)
    end)

    it("should parse UploadPart", function()
      local result = parse_s3_request(
        "PUT",
        "/bucket/key.txt",
        { uploadId = "xyz", partNumber = "1" }
      )
      assert.are.equal("s3:UploadPart/bucket/key.txt", result)
    end)

    it("should parse CompleteMultipartUpload", function()
      local result = parse_s3_request("POST", "/bucket/key.txt", { uploadId = "xyz" })
      assert.are.equal("s3:CompleteMultipartUpload/bucket/key.txt", result)
    end)

    it("should parse AbortMultipartUpload", function()
      local result = parse_s3_request("DELETE", "/bucket/key.txt", { uploadId = "xyz" })
      assert.are.equal("s3:AbortMultipartUpload/bucket/key.txt", result)
    end)

    it("should parse ListParts", function()
      local result = parse_s3_request("GET", "/bucket/key.txt", { uploadId = "xyz" })
      assert.are.equal("s3:ListParts/bucket/key.txt", result)
    end)

    it("should handle empty path", function()
      local result = parse_s3_request("GET", "/", {})
      assert.are.equal("s3:ListBucket//", result)
    end)

    it("should handle path with special characters", function()
      local result = parse_s3_request("GET", "/bucket/file%20with%20spaces.txt", {})
      assert.are.equal("s3:GetObject/bucket/file%20with%20spaces.txt", result)
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
      local grants = { "s3:GetObject/bucket/key.txt" }
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/key.txt")
      assert.is_true(allowed)
      assert.is_not_nil(string.find(reason, "matched grant"))
    end)

    it("should allow prefix match", function()
      local grants = { "s3:GetObject/bucket/uploads/" }
      local allowed, reason = authorize(grants, "s3:GetObject/bucket/uploads/file.txt")
      assert.is_true(allowed)
      assert.is_not_nil(string.find(reason, "matched grant"))
    end)

    it("should allow nested prefix match", function()
      local grants = { "s3:GetObject/bucket/uploads/" }
      local allowed = authorize(grants, "s3:GetObject/bucket/uploads/user123/file.txt")
      assert.is_true(allowed)
    end)

    it("should deny different action", function()
      local grants = { "s3:GetObject/bucket/key.txt" }
      local allowed, reason = authorize(grants, "s3:PutObject/bucket/key.txt")
      assert.is_false(allowed)
      assert.is_not_nil(string.find(reason, "no matching grant"))
    end)

    it("should deny different bucket", function()
      local grants = { "s3:GetObject/bucket1/key.txt" }
      local allowed = authorize(grants, "s3:GetObject/bucket2/key.txt")
      assert.is_false(allowed)
    end)

    it("should deny shorter path", function()
      local grants = { "s3:GetObject/bucket/uploads/user123/" }
      local allowed = authorize(grants, "s3:GetObject/bucket/uploads/")
      assert.is_false(allowed)
    end)

    it("should allow wildcard action", function()
      local grants = { "s3:*/bucket/key.txt" }
      local allowed = authorize(grants, "s3:GetObject/bucket/key.txt")
      assert.is_true(allowed)
    end)

    it("should allow wildcard path", function()
      local grants = { "s3:GetObject/bucket/" }
      local allowed = authorize(grants, "s3:GetObject/bucket/any/path/file.txt")
      assert.is_true(allowed)
    end)

    it("should check multiple grants - first matches", function()
      local grants = {
        "s3:GetObject/bucket/uploads/",
        "s3:PutObject/bucket/docs/",
      }
      local allowed = authorize(grants, "s3:GetObject/bucket/uploads/file.txt")
      assert.is_true(allowed)
    end)

    it("should check multiple grants - second matches", function()
      local grants = {
        "s3:GetObject/bucket/uploads/",
        "s3:PutObject/bucket/docs/",
      }
      local allowed = authorize(grants, "s3:PutObject/bucket/docs/file.txt")
      assert.is_true(allowed)
    end)

    it("should deny when no grants match", function()
      local grants = {
        "s3:GetObject/bucket/uploads/",
        "s3:PutObject/bucket/docs/",
      }
      local allowed = authorize(grants, "s3:GetObject/bucket/private/file.txt")
      assert.is_false(allowed)
    end)

    it("should deny with empty grants", function()
      local allowed, reason = authorize({}, "s3:GetObject/bucket/key.txt")
      assert.is_false(allowed)
      assert.is_not_nil(string.find(reason, "no grants"))
    end)

    it("should allow multipart workflow with wildcard", function()
      local grants = { "s3:*/bucket/large-file.bin" }

      local allowed1 = authorize(grants, "s3:InitiateMultipartUpload/bucket/large-file.bin")
      assert.is_true(allowed1)

      local allowed2 = authorize(grants, "s3:UploadPart/bucket/large-file.bin")
      assert.is_true(allowed2)

      local allowed3 = authorize(grants, "s3:CompleteMultipartUpload/bucket/large-file.bin")
      assert.is_true(allowed3)
    end)

    it("should be case-sensitive", function()
      local grants = { "s3:GetObject/bucket/UPLOADS/" }
      local allowed = authorize(grants, "s3:GetObject/bucket/uploads/file.txt")
      assert.is_false(allowed)
    end)

    it("should handle grant without trailing slash", function()
      local grants = { "s3:GetObject/bucket/uploads" }
      local allowed = authorize(grants, "s3:GetObject/bucket/uploads/file.txt")
      assert.is_true(allowed)
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
