import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import folium
from streamlit_folium import st_folium

# -------------------------------------------
# ページ設定
# -------------------------------------------
st.set_page_config(page_title="AI日報解析クラウド", layout="wide")
st.title("🌲 AI日報 解析ダッシュボード v2.1")
st.markdown("アップロードした１日分のGPSログ（CSV）から’速度’を割り出し「手作業」「重機」「車両」3パターンに当てはめて各作業時間を自動解析します。")

# -------------------------------------------
# 1. ファイルアップロード
# -------------------------------------------
uploaded_file = st.file_uploader("CSVファイルをここにドラッグ＆ドロップ", type="csv")

if uploaded_file is not None:
    try:
        # CSV読み込み
        df = pd.read_csv(uploaded_file)
        
        # 列名修正
        rename_map = {'日時': 'time', '緯度': 'lat', '経度': 'lon'}
        df = df.rename(columns=rename_map)
        
        if not {'time', 'lat', 'lon'}.issubset(df.columns):
            st.error("エラー: CSVに必要な列（time, lat, lon）がありません。")
            st.stop()

        # 時間変換・ソート
        df['time'] = pd.to_datetime(df['time'])
        df = df.sort_values('time')
        
        # -------------------------------------------
        # 2. 解析ロジック（3ゾーン判定）
        # -------------------------------------------
        
        def calc_distance(lat1, lon1, lat2, lon2):
            R = 6371000
            dlat = np.radians(lat2 - lat1)
            dlon = np.radians(lon2 - lon1)
            a = np.sin(dlat/2)**2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon/2)**2
            c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
            return R * c

        df['dist_m'] = calc_distance(df['lat'].shift(), df['lon'].shift(), df['lat'], df['lon'])
        df['time_diff'] = df['time'].diff().dt.total_seconds()
        df = df.fillna(0)
        
        # 速度計算 (データ飛び対策: 600秒以上の空白は速度0扱い)
        df['speed_kmh'] = np.where((df['time_diff'] > 0) & (df['time_diff'] < 600), 
                                   (df['dist_m'] / df['time_diff']) * 3.6, 0)

        # 3パターン判定
        def classify_status(speed):
            if speed < 1.5:
                return '手作業(滞在)'   # 緑
            elif speed < 15.0:
                return '重機(クローラ)' # 橙
            else:
                return '車両(ホイール)' # 赤
        
        df['status'] = df['speed_kmh'].apply(classify_status)

        # -------------------------------------------
        # 3. 集計処理
        # -------------------------------------------
        df['group_id'] = (df['status'] != df['status'].shift()).cumsum()
        
        summary = df.groupby(['group_id', 'status']).agg(
            start_time=('time', 'first'),
            end_time=('time', 'last'),
            duration_sec=('time_diff', 'sum')
        ).reset_index()
        
        summary['duration_min'] = summary['duration_sec'] / 60
        summary = summary[summary['duration_min'] > 1] # 1分未満は無視

        # KPI集計
        total_time = df['time_diff'].sum() / 60
        total_dist = df['dist_m'].sum() / 1000
        
        time_hand = summary[summary['status']=='手作業(滞在)']['duration_min'].sum()
        time_crawler = summary[summary['status']=='重機(クローラ)']['duration_min'].sum()
        time_wheel = summary[summary['status']=='車両(ホイール)']['duration_min'].sum()

        # -------------------------------------------
        # 4. 画面表示
        # -------------------------------------------
        
        # 色と並び順の定義（ここが重要！）
        color_map = {
            '手作業(滞在)': '#66bb6a',   # 緑
            '重機(クローラ)': '#ffa726', # オレンジ
            '車両(ホイール)': '#ef5350'  # 赤
        }
        # 表示順序を固定するリスト
        order_list = ['手作業(滞在)', '重機(クローラ)', '車両(ホイール)']

        # KPIカード
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🌲 手作業(滞在)", f"{int(time_hand)}分")
        c2.metric("🚜 重機(クローラ)", f"{int(time_crawler)}分")
        c3.metric("🚚 車両(ホイール)", f"{int(time_wheel)}分")
        c4.metric("総移動距離", f"{total_dist:.1f}km")

        st.divider()

        # レイアウト
        row1_col1, row1_col2 = st.columns([1, 1])

        with row1_col1:
            st.subheader("📊 作業バランス")
            
            # 円グラフ
            if total_time > 0:
                df_pie = pd.DataFrame({
                    'status': ['手作業(滞在)', '重機(クローラ)', '車両(ホイール)'],
                    'minutes': [time_hand, time_crawler, time_wheel]
                })
                # 0分の項目は消す
                df_pie = df_pie[df_pie['minutes'] > 0]
                
                # 並び順を指定してソート（円グラフ用）
                df_pie['status'] = pd.Categorical(df_pie['status'], categories=order_list, ordered=True)
                df_pie = df_pie.sort_values('status')

                fig_pie = px.pie(df_pie, values='minutes', names='status', 
                                 title='作業時間の割合',
                                 color='status',
                                 color_discrete_map=color_map,
                                 category_orders={'status': order_list}) # 順序固定
                st.plotly_chart(fig_pie, use_container_width=True)
            
            # タイムライン
            st.write("▼ タイムライン")
            if len(summary) > 0:
                fig_timeline = px.timeline(summary, x_start="start_time", x_end="end_time", 
                                           y="status", color="status",
                                           color_discrete_map=color_map,
                                           hover_data=["duration_min"],
                                           category_orders={'status': order_list}) # 順序固定
                
                # Y軸の順序を反転（手作業を上に）
                fig_timeline.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_timeline, use_container_width=True)

        with row1_col2:
            st.subheader("🗺️ 現場マップ")
            
            center_lat = df['lat'].mean()
            center_lon = df['lon'].mean()
            m = folium.Map(location=[center_lat, center_lon], zoom_start=14)
            
            # 軌跡（全体）
            coords = df[['lat', 'lon']].values.tolist()
            if len(coords) > 0:
                folium.PolyLine(coords, color="blue", weight=3, opacity=0.5).add_to(m)
                
                # 「手作業」の場所だけ緑の点を打つ
                hand_df = df[df['status'] == '手作業(滞在)']
                for _, row in hand_df.iloc[::5].iterrows():
                    folium.CircleMarker(
                        location=[row['lat'], row['lon']],
                        radius=3,
                        color='#66bb6a',
                        fill=True,
                        fill_opacity=0.7,
                        popup=f"{row['time'].strftime('%H:%M')}"
                    ).add_to(m)
                
                # 開始・終了
                folium.Marker(coords[0], popup="開始", icon=folium.Icon(color='green', icon='play')).add_to(m)
                folium.Marker(coords[-1], popup="終了", icon=folium.Icon(color='red', icon='stop')).add_to(m)

            st_folium(m, width=None, height=500)

    except Exception as e:
        st.error(f"エラーが発生しました: {e}")



