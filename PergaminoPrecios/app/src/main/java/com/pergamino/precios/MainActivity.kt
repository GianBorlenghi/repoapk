package com.pergamino.precios

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.*
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import java.net.URLEncoder
import java.text.NumberFormat
import java.util.Locale

data class Producto(
    val supermercado: String,
    val nombre: String,
    val precio: Double,
    val precioOriginal: Double,
    val precioStr: String,
    val promos: List<String>,
    val promoEfectivo: String?,
    val imagen: String,
    val url: String
)

class MainActivity : AppCompatActivity() {
    private val client = OkHttpClient()
    private val sugerencias = listOf("coca cola 2.25", "leche serenisima", "yerba amanda 1kg", "fideos lucchetti", "aceite 1.5", "paty")
    private lateinit var adapter: ProdAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val etQuery = findViewById<TextInputEditText>(R.id.etQuery)
        val btnBuscar = findViewById<MaterialButton>(R.id.btnBuscar)
        val tvStatus = findViewById<TextView>(R.id.tvStatus)
        val rv = findViewById<RecyclerView>(R.id.rvResultados)
        val chipsBox = findViewById<ChipGroup>(R.id.chipsBox)

        sugerencias.forEach { txt ->
            val chip = Chip(this).apply {
                text = txt
                isCheckable = false
                setOnClickListener { etQuery.setText(txt); buscar(txt, tvStatus) }
            }
            chipsBox.addView(chip)
        }

        adapter = ProdAdapter(emptyList()) { url ->
            try {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
            } catch (e: Exception) {
                // No hay app/navegador que resuelva el link, lo ignoramos
            }
        }
        rv.layoutManager = LinearLayoutManager(this)
        rv.adapter = adapter

        btnBuscar.setOnClickListener {
            val q = etQuery.text.toString().trim()
            if (q.isNotBlank()) buscar(q, tvStatus)
        }
        etQuery.setOnEditorActionListener { _, _, _ ->
            val q = etQuery.text.toString().trim()
            if (q.isNotBlank()) buscar(q, tvStatus)
            true
        }
        // Demo inicial
        etQuery.setText("coca cola 2.25")
    }

    private fun buscar(query: String, tvStatus: TextView) {
        tvStatus.text = "Buscando \"$query\" en 3 supers…"
        lifecycleScope.launch {
            val res = withContext(Dispatchers.IO) { buscarEnTodos(query) }
            if (res.isEmpty()) {
                tvStatus.text = "Sin resultados para \"$query\""
            } else {
                val min = res.minOf { it.precio }
                tvStatus.text = "${res.size} productos • Más barato: ${formatear(min)}"
            }
            adapter.update(res)
        }
    }

    private fun formatear(v: Double): String {
        return NumberFormat.getCurrencyInstance(Locale("es", "AR")).format(v).replace("ARS", "$").trim()
    }

    // Lógica igual a core.py pero simplificada en Kotlin (sin promos complejas, pero con precio efectivo básico)
    private fun normalizar(s: String): String {
        var t = s.lowercase()
        t = java.text.Normalizer.normalize(t, java.text.Normalizer.Form.NFD).replace(Regex("\\p{Mn}"), "")
        t = t.replace(Regex("(\\d),(\\d)"), "$1.$2")
        t = t.replace(Regex("(\\d\\.?\\d*)\\s*(l|lt|lts|ml|cc|kg|k|g|gr|grs)\\b"), "$1$2")
        t = t.replace(Regex("\\s+"), " ").trim()
        return t
    }

    private suspend fun buscarEnTodos(query: String): List<Producto> = withContext(Dispatchers.IO) {
        val terminos = normalizar(query).split(" ").filter { it.length >= 2 }
        if (terminos.isEmpty()) return@withContext emptyList()
        val defs = listOf(
            "MasOnline" to "https://www.masonline.com.ar/api/catalog_system/pub/products/search?ft={q}&_from=0&_to=20",
            "VEA" to "https://www.vea.com.ar/api/catalog_system/pub/products/search?ft={q}&_from=0&_to=20",
            "Carrefour" to "https://www.carrefour.com.ar/api/catalog_system/pub/products/search?ft={q}&_from=0&_to=20"
        )
        val jobs = defs.map { (n, tpl) -> async { buscarEnSuper(n, tpl, query, terminos) } }
        jobs.awaitAll().flatten().sortedBy { it.precio }
    }

    private fun buscarEnSuper(superN: String, tpl: String, query: String, terminos: List<String>): List<Producto> {
        val url = tpl.replace("{q}", URLEncoder.encode(query, "UTF-8"))
        return try {
            val req = Request.Builder().url(url).header("User-Agent", "Mozilla/5.0").header("Accept", "application/json").build()
            val res = client.newCall(req).execute()
            if (!res.isSuccessful) return emptyList()
            val arr = JSONArray(res.body?.string() ?: "[]")
            val out = mutableListOf<Producto>()
            for (i in 0 until arr.length()) {
                try {
                    val prod = arr.getJSONObject(i)
                    val nombre = prod.optString("productName", "").trim()
                    if (nombre.isEmpty()) continue
                    val nNorm = normalizar(nombre)
                    if (!terminos.all { nNorm.contains(it) }) continue
                    val primeras = nNorm.split(" ").take(6).joinToString(" ")
                    if (terminos.none { primeras.contains(it) }) continue
                    if (nombre.contains(" + ") && !query.contains(" + ")) continue
                    var link = prod.optString("link", "")
                    if (link.startsWith("/")) link = tpl.split("/api")[0] + link
                    var imagen = ""
                    try {
                        imagen = prod.getJSONArray("items").getJSONObject(0).getJSONArray("images").getJSONObject(0).optString("imageUrl", "")
                        if (imagen.startsWith("//")) imagen = "https:$imagen"
                    } catch (e: Exception) {
                        // Sin imagen disponible para este producto
                    }
                    var mejor: Triple<Double, Double?, org.json.JSONObject>? = null
                    val items = prod.optJSONArray("items") ?: continue
                    for (j in 0 until items.length()) {
                        val sellers = items.getJSONObject(j).optJSONArray("sellers") ?: continue
                        for (k in 0 until sellers.length()) {
                            val of = sellers.getJSONObject(k).optJSONObject("commertialOffer")
                                ?: sellers.getJSONObject(k).optJSONObject("commercialOffer") ?: continue
                            val pf = of.optDouble("Price", Double.NaN)
                            if (pf.isNaN() || pf < 500) continue
                            var po = of.optDouble("ListPrice", pf)
                            if (po / pf > 3.0) po = pf
                            if (of.optInt("AvailableQuantity", 1) <= 0) continue
                            if (mejor == null || pf < mejor!!.first) mejor = Triple(pf, po, of)
                        }
                    }
                    if (mejor == null) continue
                    val (pf, po, of) = mejor!!
                    val poVal = po ?: pf
                    val promos = mutableListOf<String>()
                    val teasers = of.optJSONArray("PromotionTeasers")
                    if (teasers != null) for (t in 0 until teasers.length()) {
                        val o = teasers.optJSONObject(t) ?: continue
                        val name = o.optString("Name", "").trim()
                        if (name.length in 3..70) promos.add("🎁 $name")
                    }
                    if (promos.isEmpty() && poVal > pf && poVal / pf <= 3) {
                        val pct = ((1 - pf / poVal) * 100).toInt()
                        if (pct in 1..70) promos.add("🏷️ ${pct}% OFF")
                    }
                    // Precio efectivo simple para 2do 50% / 3x2
                    var promoEff: String? = null
                    for (p in promos) {
                        val low = p.lowercase()
                        var eff: String? = null
                        val m1 = Regex("""2do\s+al\s+(\d+)\s*%""").find(low)
                        if (m1 != null) {
                            val pct = m1.groupValues[1].toInt()
                            if (pct in 1..90) {
                                val tot = pf + pf * (1 - pct / 100.0)
                                eff = formatear(tot / 2)
                            }
                        } else if (low.contains("3x2")) eff = formatear(pf * 2 / 3)
                        else if (low.contains("2x1")) eff = formatear(pf / 2)
                        if (eff != null) {
                            promoEff = "$p → $eff c/u llevando ${if (p.contains("3x2")) "3" else "2"}"
                            break
                        }
                    }
                    val promosFinal = if (promoEff != null) listOf(promoEff) else promos.take(2)
                    out.add(Producto(superN, nombre, pf, poVal, formatear(pf), promosFinal, promoEff, imagen, link))
                    if (out.size >= 8) break
                } catch (e: Exception) {
                    // Producto individual mal formado, lo saltamos
                }
            }
            out.sortedBy { it.precio }
        } catch (e: Exception) {
            emptyList()
        }
    }

    inner class ProdAdapter(var items: List<Producto>, val onLink: (String) -> Unit) : RecyclerView.Adapter<ProdAdapter.VH>() {
        inner class VH(v: View) : RecyclerView.ViewHolder(v) {
            val tvNombre = v.findViewById<TextView>(R.id.tvNombre)
            val tvPrecio = v.findViewById<TextView>(R.id.tvPrecio)
            val tvPromo = v.findViewById<TextView>(R.id.tvPromo)
            val img = v.findViewById<ImageView>(R.id.imgProducto)
            val tvLink = v.findViewById<TextView>(R.id.tvLink)
        }

        override fun onCreateViewHolder(p: ViewGroup, t: Int) = VH(LayoutInflater.from(p.context).inflate(R.layout.item_producto, p, false))
        override fun getItemCount() = items.size
        override fun onBindViewHolder(h: VH, pos: Int) {
            val p = items[pos]
            h.tvNombre.text = p.nombre.take(48)
            h.tvPrecio.text = "💲 ${p.precioStr}"
            h.tvPromo.text = p.promos.joinToString(" • ").take(80)
            h.tvPromo.visibility = if (p.promos.isEmpty()) View.GONE else View.VISIBLE
            h.tvLink.setOnClickListener { onLink(p.url) }
            if (p.imagen.isNotBlank()) Glide.with(h.itemView).load(p.imagen).into(h.img)
        }

        fun update(n: List<Producto>) {
            items = n
            notifyDataSetChanged()
        }
    }
}
