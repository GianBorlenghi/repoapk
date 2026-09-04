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
    private lateinit var adapterPromos: PromoAdapter
    private lateinit var adapterLista: ListaAdapter
    private val listaSuper = mutableListOf<Producto>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val etQuery = findViewById<TextInputEditText>(R.id.etQuery)
        val btnBuscar = findViewById<MaterialButton>(R.id.btnBuscar)
        val tvStatus = findViewById<TextView>(R.id.tvStatus)
        val rv = findViewById<RecyclerView>(R.id.rvResultados)
        val rvPromos = findViewById<RecyclerView>(R.id.rvPromos)
        val rvLista = findViewById<RecyclerView>(R.id.rvLista)
        val layoutPromos = findViewById<View>(R.id.layoutPromos)
        val layoutLista = findViewById<View>(R.id.layoutLista)
        val chipsBox = findViewById<ChipGroup>(R.id.chipsBox)
        val bottomNav = findViewById<com.google.android.material.bottomnavigation.BottomNavigationView>(R.id.bottomNav)

        sugerencias.forEach { txt ->
            val chip = Chip(this).apply {
                text = txt
                isCheckable = false
                setOnClickListener {
                    etQuery.setText(txt)
                    buscar(txt, tvStatus)
                }
            }
            chipsBox.addView(chip)
        }

        adapter = ProdAdapter(emptyList(), 
            onLink = { url -> try { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } catch {} },
            onAgregar = { prod -> agregarALista(prod) }
        )
        rv.layoutManager = LinearLayoutManager(this)
        rv.adapter = adapter

        // Promos
        adapterPromos = PromoAdapter(emptyList())
        rvPromos.layoutManager = LinearLayoutManager(this)
        rvPromos.adapter = adapterPromos
        findViewById<MaterialButton>(R.id.btnActualizarPromos)?.setOnClickListener { cargarPromos() }

        // Lista
        adapterLista = ListaAdapter(listaSuper) { pos -> quitarDeLista(pos) }
        rvLista.layoutManager = LinearLayoutManager(this)
        rvLista.adapter = adapterLista
        findViewById<MaterialButton>(R.id.btnVaciarLista)?.setOnClickListener { vaciarLista() }

        // Navegación
        bottomNav.setOnItemSelectedListener { item ->
            when(item.itemId){
                R.id.nav_comparar -> { rv.visibility=View.VISIBLE; layoutPromos.visibility=View.GONE; layoutLista.visibility=View.GONE; true }
                R.id.nav_promos -> { rv.visibility=View.GONE; layoutPromos.visibility=View.VISIBLE; layoutLista.visibility=View.GONE; cargarPromos(); true }
                R.id.nav_lista -> { rv.visibility=View.GONE; layoutPromos.visibility=View.GONE; layoutLista.visibility=View.VISIBLE; actualizarListaUI(); true }
                else -> false
            }
        }

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

    // ── Lista de supermercado ──
    private fun agregarALista(p: Producto){
        listaSuper.add(p)
        actualizarListaUI()
        val bottomNav=findViewById<com.google.android.material.bottomnavigation.BottomNavigationView>(R.id.bottomNav)
        // Feedback
        android.widget.Toast.makeText(this, "${p.nombre.take(30)} agregado a ${p.supermercado}", android.widget.Toast.LENGTH_SHORT).show()
    }
    private fun quitarDeLista(pos:Int){
        if(pos in listaSuper.indices){
            listaSuper.removeAt(pos)
            actualizarListaUI()
        }
    }
    private fun vaciarLista(){
        listaSuper.clear()
        actualizarListaUI()
    }
    private fun actualizarListaUI(){
        val tvVacia=findViewById<TextView>(R.id.tvListaVacia)
        val tvTotal=findViewById<TextView>(R.id.tvTotalLista)
        tvVacia.visibility=if(listaSuper.isEmpty()) View.VISIBLE else View.GONE
        adapterLista.update(listaSuper.toList())
        if(listaSuper.isNotEmpty()){
            val total=listaSuper.sumOf{it.precio}
            val porSuper=listaSuper.groupBy{it.supermercado}.map{ (k,v) -> "$k: ${v.size} prod." }.joinToString(" • ")
            tvTotal.text="Total: ${formatear(total)} • $porSuper"
            // Mostrar super de cada producto en la lista ya está en el adapter
        } else {
            tvTotal.text=""
        }
    }

    // ── Promos dinámicas ──
    private fun cargarPromos(){
        val tvStatus=findViewById<TextView>(R.id.tvPromosStatus)
        tvStatus.text="Cargando promos bancarias..."
        lifecycleScope.launch{
            val promos=withContext(Dispatchers.IO){ obtenerPromosBancarias() }
            adapterPromos.update(promos)
            tvStatus.text="Promos vigentes • ${promos.size} supers"
        }
    }
    private fun obtenerPromosBancarias(): List<PromoBanco>{
        // Estáticas + dinámicas extraídas de la API (clusters con cencopay/csi)
        val estaticas=mapOf(
            "MasOnline" to listOf("Cencopay 25% + 3 CSI — Todos los días (Tope \$8.000)","BNA 30% MODO — Miércoles","Naranja X 3 cuotas — >\$40k"),
            "VEA" to listOf("Cencopay 25% + 3 CSI — Todos los días","Galicia 20% — Jueves","BBVA 15% + 3 CSI — Viernes","BNA 30% MODO — Miércoles"),
            "Carrefour" to listOf("Mi Carrefour 15% + 3 CSI — Todos los días","BNA 30% MODO — Miércoles","Macro 20% MODO — Miércoles","Santander 25% Visa MODO — Viernes")
        )
        val res=mutableListOf<PromoBanco>()
        for((superN, promos) in estaticas){
            // Intentar agregar dinámicas: buscar un query genérico y extraer clusters de pago
            val dinamicas=mutableSetOf<String>()
            try{
                val url="https://www.${if(superN=="MasOnline") "masonline" else superN.lowercase()}.com.ar/api/catalog_system/pub/products/search?ft=leche&_from=0&_to=5"
                val req=okhttp3.Request.Builder().url(url).header("User-Agent","Mozilla/5.0").build()
                val resp=client.newCall(req).execute()
                if(resp.isSuccessful){
                    val arr=org.json.JSONArray(resp.body?.string() ?: "[]")
                    for(i in 0 until minOf(arr.length(), 3)){
                        val prod=arr.optJSONObject(i)?:continue
                        val clusters=mutableListOf<String>()
                        prod.optJSONObject("productClusters")?.keys()?.forEach{ k-> clusters.add(prod.getJSONObject("productClusters").optString(k)) }
                        prod.optJSONObject("clusterHighlights")?.keys()?.forEach{ k-> clusters.add(prod.getJSONObject("clusterHighlights").optString(k)) }
                        for(c in clusters){
                            val low=c.lowercase()
                            if(listOf("cencopay","csi","cuotas","cuota","banco","tarjeta").any{low.contains(it)} && c.length<80 && !low.contains("colection")){
                                dinamicas.add(c.trim())
                            }
                        }
                    }
                }
            }catch{}
            val todas=(promos + dinamicas.take(2)).distinct().take(4)
            res.add(PromoBanco(superN, todas))
        }
        return res
    }
    data class PromoBanco(val superNombre:String, val promos:List<String>)

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
        val url = tpl.replace("{q}", URLEncoder.encode(query, "UTF-8").replace("+", "%20"))
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

    inner class ProdAdapter(var items: List<Producto>, val onLink: (String) -> Unit, val onAgregar: (Producto) -> Unit) : RecyclerView.Adapter<ProdAdapter.VH>() {
        inner class VH(v: View) : RecyclerView.ViewHolder(v) {
            val tvNombre = v.findViewById<TextView>(R.id.tvNombre)
            val tvPrecio = v.findViewById<TextView>(R.id.tvPrecio)
            val tvPromo = v.findViewById<TextView>(R.id.tvPromo)
            val img = v.findViewById<ImageView>(R.id.imgProducto)
            val tvLink = v.findViewById<TextView>(R.id.tvLink)
            val btnAgregar = v.findViewById<View>(R.id.btnAgregar) ?: v.findViewById(R.id.tvLink) // fallback si no existe
        }
        override fun onCreateViewHolder(p: ViewGroup, t: Int) = VH(LayoutInflater.from(p.context).inflate(R.layout.item_producto, p, false))
        override fun getItemCount() = items.size
        override fun onBindViewHolder(h: VH, pos: Int) {
            val p = items[pos]
            h.tvNombre.text = p.nombre.take(48)
            h.tvPrecio.text = "💲 ${p.precioStr} • ${p.supermercado}"
            h.tvPromo.text = p.promos.joinToString(" • ").take(80)
            h.tvPromo.visibility = if (p.promos.isEmpty()) View.GONE else View.VISIBLE
            h.tvLink.setOnClickListener { onLink(p.url) }
            // Botón agregar si existe
            try{ h.itemView.findViewById<View>(R.id.btnAgregar)?.setOnClickListener{ onAgregar(p) } }catch{}
            // También click largo agrega
            h.itemView.setOnLongClickListener{ onAgregar(p); true }
            if (p.imagen.isNotBlank()) Glide.with(h.itemView).load(p.imagen).into(h.img)
        }
        fun update(n: List<Producto>) { items = n; notifyDataSetChanged() }
    }

    inner class PromoAdapter(var items: List<PromoBanco>) : RecyclerView.Adapter<PromoAdapter.VH>(){
        inner class VH(v:View):RecyclerView.ViewHolder(v){
            val tvSuper=v.findViewById<TextView>(R.id.tvSuper)
            val tvCount=v.findViewById<TextView>(R.id.tvCount)
            val tv1=v.findViewById<TextView>(R.id.tvPromo1)
            val tv2=v.findViewById<TextView>(R.id.tvPromo2)
            val tv3=v.findViewById<TextView>(R.id.tvPromo3)
        }
        override fun onCreateViewHolder(p:ViewGroup,t:Int)=VH(LayoutInflater.from(p.context).inflate(R.layout.item_promo,p,false))
        override fun getItemCount()=items.size
        override fun onBindViewHolder(h:VH, pos:Int){
            val p=items[pos]
            h.tvSuper.text="🏦 ${p.superNombre}"
            h.tvCount.text="${p.promos.size} promos"
            h.tv1.text = p.promos.getOrNull(0) ?: ""
            h.tv2.text = p.promos.getOrNull(1) ?: ""
            h.tv3.text = p.promos.getOrNull(2) ?: ""
            h.tv1.visibility=if(p.promos.size>0) View.VISIBLE else View.GONE
            h.tv2.visibility=if(p.promos.size>1) View.VISIBLE else View.GONE
            h.tv3.visibility=if(p.promos.size>2) View.VISIBLE else View.GONE
        }
        fun update(n:List<PromoBanco>){ items=n; notifyDataSetChanged() }
    }

    inner class ListaAdapter(var items:List<Producto>, val onQuitar:(Int)->Unit):RecyclerView.Adapter<ListaAdapter.VH>(){
        inner class VH(v:View):RecyclerView.ViewHolder(v){
            val tvNombre=v.findViewById<TextView>(R.id.tvNombreLista)
            val tvSuper=v.findViewById<TextView>(R.id.tvSuperLista)
            val tvPrecio=v.findViewById<TextView>(R.id.tvPrecioLista)
            val img=v.findViewById<ImageView>(R.id.imgLista)
            val btn=v.findViewById<View>(R.id.btnQuitar)
        }
        override fun onCreateViewHolder(p:ViewGroup,t:Int)=VH(LayoutInflater.from(p.context).inflate(R.layout.item_lista,p,false))
        override fun getItemCount()=items.size
        override fun onBindViewHolder(h:VH, pos:Int){
            val p=items[pos]
            h.tvNombre.text=p.nombre.take(40)
            h.tvSuper.text=p.supermercado
            // Color por super
            val col=when(p.supermercado){ "MasOnline"->0xFF1565C0.toInt(); "VEA"->0xFFC62828.toInt(); else->0xFF0D47A1.toInt() }
            h.tvSuper.setBackgroundColor(col)
            h.tvPrecio.text=p.precioStr
            if(p.imagen.isNotBlank()) Glide.with(h.itemView).load(p.imagen).into(h.img)
            h.btn.setOnClickListener{ onQuitar(pos) }
        }
        fun update(n:List<Producto>){ items=n; notifyDataSetChanged() }
    }
}
