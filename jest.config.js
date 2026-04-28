// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: 'jsdom',
  testMatch: [
    '<rootDir>/tests/ts/**/*.test.ts',
    '<rootDir>/tests/ts/**/*.test.tsx'
  ],
  transform: {
    '^.+\\.tsx?$': [
      'ts-jest',
      {
        tsconfig: '<rootDir>/tests/ts/tsconfig.json'
      }
    ]
  },
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'json'],
  // tiktoken pulls in WebAssembly that doesn't load cleanly under jsdom.
  // The tests don't depend on real tokenization, so stub it out.
  moduleNameMapper: {
    '^tiktoken$': '<rootDir>/tests/ts/__mocks__/tiktoken.ts'
  },
  clearMocks: true
};
