plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

android {
    namespace = "it.rentflow.pro"
    compileSdk = 35

    defaultConfig {
        applicationId = "it.rentflow.pro"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            install("Flask>=3.0,<4.0")
            install("reportlab>=4.0,<5.0")
            install("cryptography>=42.0,<47.0")
        }
    }
}
