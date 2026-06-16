package com.example.monitorize.ui.theme

import android.os.Build
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val MonitorizeLightScheme = lightColorScheme(
    primary = BalticBlue,
    onPrimary = Color.White,
    secondary = BondiBlue,
    onSecondary = Color.White,
    tertiary = SkySurge,
    onTertiary = PrussianBlue,
    background = ElectricAqua,
    onBackground = PrussianBlue,
    surface = BondiBlue,
    onSurface = Color.White,
    surfaceVariant = SkySurge,
    onSurfaceVariant = PrussianBlue,
    outline = SkySurge
)

@Composable
fun MonitorizeTheme(
    dynamicColor: Boolean = false,
    content: @Composable () -> Unit
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            dynamicLightColorScheme(LocalContext.current)
        }
        else -> MonitorizeLightScheme
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography,
        content = content
    )
}