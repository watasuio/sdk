#!/usr/bin/env node
import { readFileSync, writeFileSync } from 'node:fs'

const versionTag = process.argv[2]
if (!versionTag || !/^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(versionTag)) {
  console.error('usage: scripts/prepare-release.mjs vX.Y.Z')
  process.exit(1)
}

const version = versionTag.slice(1)
const repository = process.env.GITHUB_REPOSITORY

replaceInFile('pyproject.toml', /^version = ".*"$/m, `version = "${version}"`)
replaceInFile('rust/Cargo.toml', /^version = ".*"$/m, `version = "${version}"`)

if (repository) {
  upsertTomlString('pyproject.toml', '[project.urls]', 'Repository', `https://github.com/${repository}`)
  upsertTomlString('rust/Cargo.toml', '[package]', 'repository', `https://github.com/${repository}`)
}

const cargoLock = readFileSync('rust/Cargo.lock', 'utf8')
writeFileSync(
  'rust/Cargo.lock',
  cargoLock.replace(
    /(\[\[package\]\]\nname = "watasu"\nversion = ")[^"]+(")/,
    `$1${version}$2`,
  ),
)

const packageJson = JSON.parse(readFileSync('ts/package.json', 'utf8'))
packageJson.version = version
if (repository) {
  packageJson.repository = {
    type: 'git',
    url: `git+https://github.com/${repository}.git`,
    directory: 'ts',
  }
}
writeJson('ts/package.json', packageJson)

const packageLock = JSON.parse(readFileSync('ts/package-lock.json', 'utf8'))
packageLock.version = version
packageLock.packages[''].version = version
if (packageJson.repository) {
  packageLock.packages[''].repository = packageJson.repository
}
writeJson('ts/package-lock.json', packageLock)

function replaceInFile(path, pattern, replacement) {
  const input = readFileSync(path, 'utf8')
  const output = input.replace(pattern, replacement)
  if (input === output) {
    console.error(`did not update ${path}`)
    process.exit(1)
  }
  writeFileSync(path, output)
}

function writeJson(path, value) {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`)
}

function upsertTomlString(path, table, key, value) {
  const input = readFileSync(path, 'utf8')
  const line = `${key} = "${value}"`
  const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const existingPattern = new RegExp(`^${escapedKey} = ".*"$`, 'm')
  if (existingPattern.test(input)) {
    writeFileSync(path, input.replace(existingPattern, line))
    return
  }

  const tableIndex = input.indexOf(`${table}\n`)
  if (tableIndex === -1) {
    writeFileSync(path, `${input.trimEnd()}\n\n${table}\n${line}\n`)
    return
  }

  const insertAt = tableIndex + table.length + 1
  writeFileSync(path, `${input.slice(0, insertAt)}${line}\n${input.slice(insertAt)}`)
}
