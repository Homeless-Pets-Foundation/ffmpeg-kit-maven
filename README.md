# FFmpeg Kit Maven Repository

Self-hosted Maven repository for `com.arthenica:ffmpeg-kit-min` Android artifacts.

## Why?

The original [arthenica/ffmpeg-kit](https://github.com/arthenica/ffmpeg-kit) project was archived and its Maven Central artifacts were subsequently removed. This repository hosts builds so existing projects can continue building.

## Artifacts

| Group | Artifact | Version | Notes |
|-------|----------|---------|-------|
| `com.arthenica` | `ffmpeg-kit-min` | `6.0-3` | **Current** — 16KB page size support (Google Play compliant) |
| `com.arthenica` | `ffmpeg-kit-min` | `6.0-2` | Legacy — 4KB alignment only |

### Version 6.0-3 (current)

Built from the `development` branch of the archived `arthenica/ffmpeg-kit` source which includes 16KB page size alignment support:

- **arm64-v8a** and **x86_64**: built with `-Wl,-z,max-page-size=16384` (16KB ELF LOAD segment alignment)
- **NDK**: r25b (25.2.9519653)
- **Fixes**: Google Play policy requirement for 16KB page size support (mandatory for Android 15+ devices)

**Last deployed:** 2026-04-11

## Usage

Add this repository to your Gradle settings (or in Expo, via `expo-build-properties` `extraMavenRepos`):

```gradle
// settings.gradle
dependencyResolutionManagement {
    repositories {
        maven { url "https://homeless-pets-foundation.github.io/ffmpeg-kit-maven" }
    }
}
```

For Expo managed workflow:

```js
// app.config.ts
['expo-build-properties', {
  android: {
    extraMavenRepos: ['https://homeless-pets-foundation.github.io/ffmpeg-kit-maven'],
  },
}]
```

## Building

Use the **Build FFmpeg Kit min (16KB page size)** GitHub Actions workflow (`.github/workflows/build-16kb.yml`) to produce new versions. Trigger it manually via `workflow_dispatch` with a version input. The workflow:

1. Fetches the reviewed `arthenica/ffmpeg-kit` commit and pinned native dependencies
2. Installs Android NDK r25b
3. Builds the `min` variant via `./android.sh`
4. Generates all Maven metadata (POM, checksums, Gradle module file)
5. On main, validates the candidate again in a clean job and opens a draft release PR; branch test builds only upload the candidate.
6. A maintainer reviews the draft, approves its workflow runs if GitHub displays the approval banner, and marks it ready before merging through existing protection. Only that merge publishes the Maven version. GitHub requires approval for [PR runs created with `GITHUB_TOKEN`](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

## License

FFmpeg Kit is licensed under [LGPL v3](https://github.com/arthenica/ffmpeg-kit/blob/main/LICENSE). This repository only hosts pre-built binary artifacts.
