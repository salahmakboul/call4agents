#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// npx call4agents crashtest <script> --test-accounts

import { main } from "./crashtest.js";

const [command, ...rest] = process.argv.slice(2);
if (command === "crashtest") {
  process.exitCode = await main(rest);
} else {
  console.log("usage: call4agents crashtest --demo | <agent script> --test-accounts [--watch name,name] [--max-crashes N] [--json] [-- script args]");
  process.exitCode = 2;
}
