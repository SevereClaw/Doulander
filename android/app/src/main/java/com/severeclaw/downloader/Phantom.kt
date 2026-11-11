package com.severeclaw.downloader

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.GenericShape
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.res.painterResource
import androidx.compose.foundation.Image
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Цвета и детали оформления «Phantom» (Persona 5 Royal): красный, чёрный, белый, острые углы. */
object P {
    val BG = Color(0xFF0B0B0B)
    val CARD = Color(0xFF161616)
    val CARD2 = Color(0xFF222222)
    val RED = Color(0xFFE60012)
    val RED_DARK = Color(0xFF8C000B)
    val WHITE = Color(0xFFF5F5F5)
    val MUTED = Color(0xFF9A9A9A)
    val GREEN = Color(0xFF3DDC84)
}

/** Косой параллелограмм — фирменная форма кнопок и плашек. */
fun slant(skew: Float = 0.25f) = GenericShape { size, _ ->
    val s = size.height * skew
    moveTo(s, 0f)
    lineTo(size.width, 0f)
    lineTo(size.width - s, size.height)
    lineTo(0f, size.height)
    close()
}

val Heavy = TextStyle(fontWeight = FontWeight.Black, fontStyle = FontStyle.Italic)

/** Кнопка: красная (главная) или белая. */
@Composable
fun PButton(text: String, modifier: Modifier = Modifier, primary: Boolean = true,
            enabled: Boolean = true, height: Dp = 44.dp, hPad: Dp = 18.dp, onClick: () -> Unit) {
    val bg = when {
        !enabled -> P.CARD2
        primary -> P.RED
        else -> P.WHITE
    }
    Box(
        modifier
            .height(height)
            .clip(slant())
            .background(bg)
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = hPad),
        contentAlignment = Alignment.Center,
    ) {
        Text(text.uppercase(), color = if (primary || !enabled) P.WHITE else Color.Black,
            style = Heavy, fontSize = 14.sp, maxLines = 1)
    }
}

/** Переключатель-плашка: выбранная — красная, остальные — с белой рамкой. */
@Composable
fun PChip(text: String, selected: Boolean, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Box(
        modifier
            .padding(end = 6.dp, bottom = 6.dp)
            .height(34.dp)
            .clip(slant(0.3f))
            .background(if (selected) P.RED else P.CARD2)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = P.WHITE, fontSize = 13.sp,
            fontWeight = if (selected) FontWeight.Black else FontWeight.Medium,
            fontStyle = if (selected) FontStyle.Italic else FontStyle.Normal, maxLines = 1)
    }
}

/** Заголовок раздела — красная косая плашка, нарисованная так же, как в программе для Windows. */
@Composable
fun PTitle(res: Int, modifier: Modifier = Modifier) {
    Image(painterResource(res), contentDescription = null, modifier = modifier.height(30.dp),
        contentScale = ContentScale.FillHeight)
}

/** Подзаголовок внутри экрана. */
@Composable
fun PLabel(text: String, modifier: Modifier = Modifier) {
    Text(text.uppercase(), color = P.MUTED, style = Heavy, fontSize = 12.sp,
        modifier = modifier.padding(top = 10.dp, bottom = 4.dp))
}

/** Карточка: тёмная, с красной полосой слева. */
@Composable
fun PCard(modifier: Modifier = Modifier, stripe: Color = P.RED, content: @Composable () -> Unit) {
    Column(
        modifier.fillMaxWidth().background(P.CARD)
            .drawBehind { drawRect(stripe, size = Size(4.dp.toPx(), size.height)) }
            .padding(start = 4.dp)
    ) { content() }
}

@Composable
fun PField(value: String, onChange: (String) -> Unit, placeholder: String,
           modifier: Modifier = Modifier, onDone: (() -> Unit)? = null, singleLine: Boolean = true) {
    OutlinedTextField(
        value = value, onValueChange = onChange, modifier = modifier,
        placeholder = { Text(placeholder, color = P.MUTED) },
        singleLine = singleLine,
        shape = RectangleShape,
        keyboardOptions = KeyboardOptions(imeAction = if (onDone != null) ImeAction.Done else ImeAction.Default),
        keyboardActions = KeyboardActions(onDone = { onDone?.invoke() }),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = P.RED, unfocusedBorderColor = P.RED_DARK,
            focusedTextColor = P.WHITE, unfocusedTextColor = P.WHITE, cursorColor = P.RED,
            focusedContainerColor = Color.Black, unfocusedContainerColor = Color.Black,
        ),
    )
}

/** Шапка: логотип и «вырезанные» буквы DOWNLOADER. */
@Composable
fun Banner() {
    Image(painterResource(R.drawable.banner), contentDescription = "Downloader",
        modifier = Modifier.fillMaxWidth().padding(top = 6.dp, start = 4.dp, end = 4.dp),
        contentScale = ContentScale.FillWidth)
}

/** Тонкая рамка выделения. */
fun Modifier.pBorder(on: Boolean) = if (on) this.border(2.dp, P.RED) else this
