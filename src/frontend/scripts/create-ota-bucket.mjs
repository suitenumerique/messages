// Create the public OTA bucket and grant anonymous read on its objects. Used in
// development (`make mobile-ota-bucket`) to bootstrap the RustFS bucket the mobile app
// fetches bundles/manifest from. Public artifacts only — never sensitive data.
//
// Usage: node scripts/create-ota-bucket.mjs
import {
  CreateBucketCommand,
  HeadBucketCommand,
  PutBucketPolicyCommand,
} from "@aws-sdk/client-s3";

import { otaConfig } from "./ota-lib.mjs";

const { client, bucket } = otaConfig();

try {
  await client.send(new HeadBucketCommand({ Bucket: bucket }));
  console.log(`Bucket '${bucket}' already exists.`);
} catch {
  await client.send(new CreateBucketCommand({ Bucket: bucket }));
  console.log(`Bucket '${bucket}' created.`);
}

await client.send(
  new PutBucketPolicyCommand({
    Bucket: bucket,
    Policy: JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Sid: "PublicRead",
          Effect: "Allow",
          Principal: "*",
          Action: "s3:GetObject",
          Resource: `arn:aws:s3:::${bucket}/*`,
        },
      ],
    }),
  }),
);
console.log(`Bucket '${bucket}' is now publicly readable.`);
