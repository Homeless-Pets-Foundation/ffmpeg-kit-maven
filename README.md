# FFmpeg Kit Maven Repository

Self-hosted Maven repository for `com.arthenica:ffmpeg-kit-min` Android artifacts.

## Why?

The original [arthenica/ffmpeg-kit](https://github.com/arthenica/ffmpeg-kit) project was archived and its Maven Central artifacts were subsequently removed. This repository hosts the last published version so existing projects can continue building.

## Artifacts

| Group | Artifact | Version | Source |
|-------|----------|---------|--------|
| `com.arthenica` | `ffmpeg-kit-min` | `6.0-2` | Retrieved from Alibaba Maven Central mirror |

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

## Verification

The AAR checksum matches the original Maven Central artifact:
- SHA-256 of `ffmpeg-kit-min-6.0-2.aar`: see `.sha256` file

## License

FFmpeg Kit is licensed under [LGPL v3](https://github.com/arthenica/ffmpeg-kit/blob/main/LICENSE). This repository only hosts the pre-built binary artifact.
