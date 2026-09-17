pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories { google(); mavenCentral() }
}

rootProject.name = "jev-mobile-bridge"
include(":app")
include(":test-app")
project(":test-app").projectDir = file("../jev-mobile-test-app")
