import js from '@eslint/js';
import globals from 'globals';

export default [
  js.configs.recommended,
  {
    ignores: [
      'test/explore-site.js',
      'test/explore-*.js',
      'test/migrate_to_firestore.js',
    ],
  },
  {
    files: ['src/**/*.js', 'test/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'commonjs',
      globals: {
        ...globals.node,
      },
    },
    rules: {
      'no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
      'no-console': 'off',
    },
  },
  {
    files: ['src/browser/**/*.js'],
    rules: {
      // Code inside page.evaluate() references browser globals.
      'no-undef': 'off',
    },
  },
];
