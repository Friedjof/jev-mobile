plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "io.jev.mobile.fixture"
    compileSdk = 34
    defaultConfig { applicationId = "io.jev.mobile.fixture"; minSdk = 26; targetSdk = 34; versionCode = 1; versionName = "0.1.0" }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
}
kotlin { jvmToolchain(17) }
